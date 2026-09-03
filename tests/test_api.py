from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "environment": "test",
        "classifier": "rules",
    }


def test_create_streetlight_complaint_redacts_and_auto_routes(client: TestClient) -> None:
    raw_phone = "010-1234-5678"
    response = client.post(
        "/api/v1/complaints",
        json={
            "title": f"가로등 고장 {raw_phone}",
            "content": f"정자동 공원 입구 가로등 불이 꺼졌습니다. 연락처 {raw_phone}",
            "location_text": "정자동 공원 입구",
            "channel": "web",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "assigned"
    assert body["category"] == "streetlight"
    assert body["assigned_department_id"] == "ROAD_LIGHTING"
    assert body["routing_confidence"] >= 0.90
    assert raw_phone not in response.text
    assert body["redacted_title"].endswith("[전화번호]")
    assert "[전화번호]" in body["redacted_content"]
    assert "mobile_phone" in body["pii_types"]
    assert body["redacted_location_text"] == "정자동 공원 입구"
    assert "content" not in body
    assert "title" not in body
    assert "KB-STREETLIGHT-001" in body["knowledge_source_ids"]

    detail = client.get(f"/api/v1/complaints/{body['id']}")
    assert detail.status_code == 200
    actions = [event["action"] for event in detail.json()["audit_events"]]
    assert actions == ["complaint_received", "pii_redacted", "triage_completed"]


def test_sensitive_welfare_case_never_auto_routes(client: TestClient) -> None:
    response = client.post(
        "/api/v1/complaints",
        json={
            "title": "복지 지원 대상 문의",
            "content": "제가 기초생활 수급자 지원 대상인지 자동으로 결정해 주세요.",
            "channel": "call_center",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["category"] == "welfare"
    assert body["status"] == "needs_review"
    assert body["requires_human_review"] is True
    assert body["assigned_department_id"] is None


def test_emergency_signal_forces_urgent_human_review(client: TestClient) -> None:
    response = client.post(
        "/api/v1/complaints",
        json={
            "title": "가스 누출 의심",
            "content": "배관에서 가스 냄새가 심하고 누출되는 것 같습니다.",
            "location_text": "서현동 데모 건물 앞",
            "channel": "sms",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "urgent_review"
    assert body["urgency"] == "critical"
    assert body["assigned_department_id"] is None
    assert "gas_leak" in body["emergency_signals"]


def test_human_can_approve_route_without_external_send(client: TestClient) -> None:
    created = client.post(
        "/api/v1/complaints",
        json={
            "title": "소관을 모르겠습니다",
            "content": "여러 부서에 문의했는데 어디인지 잘 모르겠습니다.",
            "channel": "web",
        },
    ).json()

    approval = client.post(
        f"/api/v1/complaints/{created['id']}/approve",
        json={
            "department_id": "CIVIL_COORDINATION",
            "answer_draft": "담당자가 내용을 확인하겠습니다. 외부 발송은 하지 않습니다.",
            "actor_id": "test-officer",
        },
    )

    assert approval.status_code == 200
    body = approval.json()
    assert body["status"] == "reviewed"
    assert body["assigned_department_id"] == "CIVIL_COORDINATION"
    assert body["reviewed_by"] == "test-officer"

    detail = client.get(f"/api/v1/complaints/{created['id']}").json()
    assert detail["audit_events"][-1]["action"] == "human_review_approved"


def test_missing_location_blocks_auto_route(client: TestClient) -> None:
    response = client.post(
        "/api/v1/complaints",
        json={
            "title": "가로등 고장",
            "content": "가로등 불이 꺼졌습니다.",
            "channel": "web",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "needs_review"
    assert body["assigned_department_id"] is None
    assert body["missing_information"]


def test_list_endpoint_never_exposes_raw_body(client: TestClient) -> None:
    secret = "citizen-secret@example.com"
    client.post(
        "/api/v1/complaints",
        json={
            "title": "쓰레기 수거 문의",
            "content": f"쓰레기 수거가 되지 않았습니다. {secret}",
            "location_text": "금광동 데모 위치",
            "channel": "web",
        },
    )

    response = client.get("/api/v1/complaints")

    assert response.status_code == 200
    assert secret not in response.text
    assert "content" not in response.json()[0]


def test_whitespace_only_complaint_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/complaints",
        json={
            "title": "   ",
            "content": "      ",
            "channel": "web",
        },
    )

    assert response.status_code == 422


def test_high_confidence_penalty_request_still_requires_human_review(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/complaints",
        json={
            "title": "불법주정차 과태료 취소 요청",
            "content": "불법주정차 과태료와 주차 단속 처분을 자동으로 취소해 주세요.",
            "location_text": "정자동 데모 도로",
            "channel": "web",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["routing_confidence"] >= 0.90
    assert body["status"] == "needs_review"
    assert body["assigned_department_id"] is None
    assert body["requires_human_review"] is True
