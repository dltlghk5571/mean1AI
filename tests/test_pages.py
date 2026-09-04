from fastapi.testclient import TestClient


def test_home_page_loads(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "성남 민원 업무지원" in response.text
    assert "시연용 시스템" in response.text
    assert "민원 처리 현황" in response.text
    assert "처리 원칙" in response.text
    assert "data-open-intake" in response.text
    assert "분류 방식: 규칙 기반" in response.text
    assert "외부 시스템 연결 없음" in response.text


def test_html_flow_does_not_render_raw_phone(client: TestClient) -> None:
    phone = "010-1111-2222"
    response = client.post(
        "/submit",
        data={
            "title": f"가로등 문의 {phone}",
            "content": f"가로등 불이 꺼졌습니다. {phone}",
            "location_text": "가상 시험동 데모 위치",
            "channel": "web",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert phone not in response.text
    assert "[전화번호]" in response.text
    assert "담당자 검토·승인" in response.text
    assert "외부 발송 없음" in response.text
    assert "data-preview-draft" in response.text
    assert "data-confirm-approval" in response.text
    assert "[답변 초안 — 담당자 검토 및 수정 필요]" in response.text
    assert "REDACTED INTAKE" not in response.text


def test_review_queue_filter_only_shows_human_review_cases(client: TestClient) -> None:
    assigned_title = "자동 배정 전용 가로등 사례"
    review_title = "사람 검토 전용 복지 사례"
    client.post(
        "/submit",
        data={
            "title": assigned_title,
            "content": "가로등 조명이 모두 불이 꺼져 있습니다.",
            "location_text": "가상 시험동 1번 위치",
            "channel": "web",
        },
    )
    client.post(
        "/submit",
        data={
            "title": review_title,
            "content": "기초생활 복지 지원 대상인지 검토해 주세요.",
            "location_text": "",
            "channel": "web",
        },
    )

    response = client.get("/?status=review")

    assert response.status_code == 200
    assert "검토 대기 민원" in response.text
    assert review_title in response.text
    assert assigned_title not in response.text


def test_detail_page_explains_local_duplicate_candidates(client: TestClient) -> None:
    earlier_title = "가상 같은 장소 첫 도로 신고"
    earlier = client.post(
        "/api/v1/complaints",
        json={
            "title": earlier_title,
            "content": "보도블록이 들떠 있어 시설 점검을 요청합니다.",
            "location_text": "가상 화면시험동 7번 지점",
            "channel": "web",
        },
    ).json()
    current = client.post(
        "/api/v1/complaints",
        json={
            "title": "가상 같은 장소 두 번째 도로 신고",
            "content": "아스팔트 도로 파임이 있어 보행이 불편합니다.",
            "location_text": "가상 화면시험동, 7번 지점",
            "channel": "web",
        },
    ).json()

    response = client.get(f"/complaints/{current['id']}")

    assert response.status_code == 200
    assert "위치 확인 및 유사 민원 후보" in response.text
    assert "외부 지도 없이" in response.text
    assert "자동 병합·자동 종결 없음" in response.text
    assert "정규화 위치 일치" in response.text
    assert earlier_title in response.text
    assert f"/complaints/{earlier['id']}" in response.text
    assert "중복 후보로 확인" in response.text
    assert "서로 다른 민원" in response.text
