from fastapi.testclient import TestClient

from app.schemas import ClassificationCandidate, ClassificationResult, Urgency


def _authenticate(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "review.demo", "password": "review-demo-2026"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    session = client.get("/api/v1/session")
    client.headers["X-CSRF-Token"] = session.json()["csrf_token"]


class SpyClassifier:
    provider_name = "spy"

    def __init__(self) -> None:
        self.seen_title = ""
        self.seen_text = ""
        self.seen_location = ""

    def classify(self, *, title: str, text: str, location_text: str | None) -> ClassificationResult:
        self.seen_title = title
        self.seen_text = text
        self.seen_location = location_text or ""
        return ClassificationResult(
            category="other",
            subcategory="테스트",
            urgency=Urgency.NORMAL,
            candidates=[
                ClassificationCandidate(
                    department_id="CIVIL_COORDINATION",
                    confidence=0.3,
                    reason="테스트",
                )
            ],
            missing_information=[],
            requires_human_review=True,
            evidence_summary="테스트",
            provider=self.provider_name,
        )


def test_provider_receives_only_redacted_title_and_body(test_app) -> None:
    spy = SpyClassifier()
    test_app.state.pipeline.classifier = spy
    phone = "010-9876-5432"
    email = "person@example.com"
    location_phone = "010-2222-3333"

    with TestClient(test_app) as client:
        _authenticate(client)
        response = client.post(
            "/api/v1/complaints",
            json={
                "title": f"문의 {phone}",
                "content": f"연락처는 {email}이고 쓰레기 문제가 있습니다.",
                "location_text": f"데모 위치 {location_phone}",
                "channel": "web",
            },
        )

    assert response.status_code == 201
    assert phone not in spy.seen_title
    assert email not in spy.seen_text
    assert "[전화번호]" in spy.seen_title
    assert "[이메일]" in spy.seen_text
    assert location_phone not in spy.seen_location
    assert "[전화번호]" in spy.seen_location


class ModelUrgencyClassifier:
    provider_name = "model-urgency-test"

    def classify(self, *, title: str, text: str, location_text: str | None) -> ClassificationResult:
        return ClassificationResult(
            category="streetlight",
            subcategory="모델 감지 긴급 상황",
            urgency=Urgency.HIGH,
            candidates=[
                ClassificationCandidate(
                    department_id="ROAD_LIGHTING",
                    confidence=0.99,
                    reason="모델이 긴급 신호를 감지함",
                )
            ],
            missing_information=[],
            requires_human_review=False,
            evidence_summary="모델 긴급도 테스트",
            provider=self.provider_name,
        )


def test_model_reported_urgency_alone_blocks_auto_route(test_app) -> None:
    test_app.state.pipeline.classifier = ModelUrgencyClassifier()

    with TestClient(test_app) as client:
        _authenticate(client)
        response = client.post(
            "/api/v1/complaints",
            json={
                "title": "시설 이상",
                "content": "규칙 사전에는 없는 표현입니다.",
                "location_text": "데모 위치",
                "channel": "web",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["urgency"] == "high"
    assert body["status"] == "urgent_review"
    assert body["assigned_department_id"] is None
    assert body["requires_human_review"] is True
