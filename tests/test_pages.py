from fastapi.testclient import TestClient


def test_home_page_loads(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "성남 민원 AI 코파일럿" in response.text
    assert "시연용 시스템" in response.text


def test_html_flow_does_not_render_raw_phone(client: TestClient) -> None:
    phone = "010-1111-2222"
    response = client.post(
        "/submit",
        data={
            "title": f"가로등 문의 {phone}",
            "content": f"가로등 불이 꺼졌습니다. {phone}",
            "location_text": "정자동 데모 위치",
            "channel": "web",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert phone not in response.text
    assert "[전화번호]" in response.text
    assert "담당자 검토·승인" in response.text
