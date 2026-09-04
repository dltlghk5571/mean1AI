from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.models import Complaint
from app.services.duplicates import normalize_location, score_duplicate


def _create_complaint(
    client: TestClient,
    *,
    title: str,
    content: str,
    location: str,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/complaints",
        json={
            "title": title,
            "content": content,
            "location_text": location,
            "channel": "web",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_location_normalization_is_deterministic_and_local() -> None:
    assert normalize_location("  가상-시험로  12번! ") == "가상 시험로 12번"
    assert normalize_location("가상－시험로　12번") == "가상 시험로 12번"
    assert normalize_location("  ") is None


def test_same_place_different_wording_is_suggested(client: TestClient) -> None:
    synthetic_email = "duplicate-fixture@example.com"
    earlier = _create_complaint(
        client,
        title="보도블록 들뜸 신고",
        content=f"보도블록 여러 장이 들떠 보행이 불편합니다. {synthetic_email}",
        location="가상 중복시험동 10번 지점",
    )
    current = _create_complaint(
        client,
        title="도로 바닥 파손 점검",
        content="아스팔트 표면에 깊은 도로 파임이 있어 확인을 요청합니다.",
        location="가상 중복시험동, 10번 지점",
    )

    response = client.get(f"/api/v1/complaints/{current['id']}/duplicate-candidates")

    assert response.status_code == 200
    assert synthetic_email not in response.text
    candidates = response.json()
    assert [candidate["candidate_complaint_id"] for candidate in candidates] == [earlier["id"]]
    assert candidates[0]["score_breakdown"]["category"] == 1.0
    assert candidates[0]["score_breakdown"]["location"] == 1.0
    assert "정규화 위치 일치" in candidates[0]["evidence"]
    assert candidates[0]["review_status"] == "suggested"


def test_same_wording_at_different_places_is_not_suggested(client: TestClient) -> None:
    wording = "가로등 조명이 모두 꺼져 야간 통행이 어렵습니다."
    _create_complaint(
        client,
        title="가로등 반복 고장",
        content=wording,
        location="가상 동편시험동 1번 위치",
    )
    current = _create_complaint(
        client,
        title="가로등 반복 고장",
        content=wording,
        location="가상 서편시험동 9번 위치",
    )

    response = client.get(f"/api/v1/complaints/{current['id']}/duplicate-candidates")

    assert response.status_code == 200
    assert response.json() == []


def test_outside_time_window_is_not_eligible() -> None:
    now = datetime.now(UTC)
    current = Complaint(
        id="current",
        created_at=now,
        redacted_title="가로등 고장",
        redacted_content="가로등 불이 꺼졌습니다.",
        redacted_location_text="가상 시간시험동 1번 위치",
        category="streetlight",
    )
    old = Complaint(
        id="old",
        created_at=now - timedelta(days=31),
        redacted_title="가로등 고장",
        redacted_content="가로등 불이 꺼졌습니다.",
        redacted_location_text="가상 시간시험동 1번 위치",
        category="streetlight",
    )

    score = score_duplicate(current, old)

    assert score.category == 1.0
    assert score.location == 1.0
    assert score.text == 1.0
    assert score.time == 0.0
    assert score.eligible is False


def test_location_number_boundaries_do_not_collapse_into_same_place() -> None:
    now = datetime.now(UTC)
    current = Complaint(
        id="current-numbered-location",
        created_at=now,
        redacted_title="도로 파손",
        redacted_content="도로 파임을 확인해 주세요.",
        redacted_location_text="가상 숫자시험로 12-3",
        category="road_damage",
    )
    candidate = Complaint(
        id="candidate-numbered-location",
        created_at=now,
        redacted_title="도로 파손",
        redacted_content="도로 파임을 확인해 주세요.",
        redacted_location_text="가상 숫자시험로 123",
        category="road_damage",
    )

    score = score_duplicate(current, candidate)

    assert score.location == 0.0
    assert score.eligible is False


def test_location_confirmation_is_audited(client: TestClient) -> None:
    complaint = _create_complaint(
        client,
        title="공원 벤치 점검",
        content="공원 벤치가 흔들려 시설 점검을 요청합니다.",
        location="가상 위치확인동 3번 공원",
    )

    before = client.get(f"/api/v1/complaints/{complaint['id']}/location")
    assert before.status_code == 200
    assert before.json()["status"] == "unconfirmed"

    confirmed = client.post(
        f"/api/v1/complaints/{complaint['id']}/location/confirm",
        json={"actor_id": "synthetic-location-reviewer"},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["confirmed_by"] == "synthetic-location-reviewer"
    detail = client.get(f"/api/v1/complaints/{complaint['id']}").json()
    assert detail["audit_events"][-1]["action"] == "location_confirmed"


def test_duplicate_decisions_never_merge_close_or_reassign_complaints(
    client: TestClient,
) -> None:
    first = _create_complaint(
        client,
        title="첫 번째 보안등 신고",
        content="보안등 불이 꺼져 점검이 필요합니다.",
        location="가상 결정시험동 5번 골목",
    )
    second = _create_complaint(
        client,
        title="두 번째 조명 신고",
        content="골목 조명 시설이 작동하지 않습니다.",
        location="가상 결정시험동 5번 골목",
    )
    current = _create_complaint(
        client,
        title="세 번째 가로등 신고",
        content="가로등이 깜빡여 야간 확인을 요청합니다.",
        location="가상 결정시험동 5번 골목",
    )
    current_before = client.get(f"/api/v1/complaints/{current['id']}").json()
    related_before = {
        complaint_id: client.get(f"/api/v1/complaints/{complaint_id}").json()
        for complaint_id in (first["id"], second["id"])
    }

    confirmed = client.post(
        f"/api/v1/complaints/{current['id']}/duplicate-candidates/{first['id']}/decision",
        json={"decision": "confirmed", "actor_id": "synthetic-duplicate-reviewer"},
    )
    rejected = client.post(
        f"/api/v1/complaints/{current['id']}/duplicate-candidates/{second['id']}/decision",
        json={"decision": "rejected", "actor_id": "synthetic-duplicate-reviewer"},
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["review_status"] == "confirmed"
    assert rejected.status_code == 200
    assert rejected.json()["review_status"] == "rejected"

    current_after = client.get(f"/api/v1/complaints/{current['id']}").json()
    assert current_after["status"] == current_before["status"]
    assert current_after["assigned_department_id"] == current_before["assigned_department_id"]
    for complaint_id, before in related_before.items():
        after = client.get(f"/api/v1/complaints/{complaint_id}").json()
        assert after["status"] == before["status"]
        assert after["assigned_department_id"] == before["assigned_department_id"]

    actions = [event["action"] for event in current_after["audit_events"]]
    assert "duplicate_candidate_confirmed" in actions
    assert "duplicate_candidate_rejected" in actions
    for action in ("merged", "closed", "finalized"):
        assert action not in actions
