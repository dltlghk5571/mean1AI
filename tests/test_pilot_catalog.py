from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import ServiceCatalogReview, ServiceCatalogVersion
from app.service_data_schemas import ServiceBundle
from app.services.service_catalog import active_catalog


def test_pilot_candidate_stays_private_and_keeps_review_evidence(
    test_app: FastAPI, anonymous_client: TestClient, client: TestClient
) -> None:
    path = "/api/v1/service-catalogs/candidates/seongnam-pilot"
    assert anonymous_client.get(path).status_code == 401
    response = client.get(path)
    assert response.status_code == 200
    bundle = ServiceBundle.model_validate(response.json())
    assert len(bundle.services) == 12
    assert all(
        doc.retrieval_use == doc.training_use == "unknown" and doc.fetched_at is None
        for doc in bundle.documents
    )
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(ServiceCatalogVersion.version))) == 0

    imported = client.post("/api/v1/service-catalogs", json=response.json())
    assert imported.status_code == 201
    page = client.get(f"/staff/service-catalogs/{bundle.version}")
    assert page.status_code == 200
    assert "분당구 &gt; 구조물관리과 &gt; 소하천관리팀" in page.text
    assert "조직·관할" in page.text and "세부 구역 재확인" in page.text
    assert "부서 표기가 달라 조직 매핑을 보류" in page.text
    assert "법정 구비서류를 뜻하지 않습니다" in page.text
    assert 'value="approved" disabled' in page.text

    decision = client.post(
        f"/api/v1/service-catalogs/{bundle.version}/review",
        json={
            "content_hash": imported.json()["content_hash"],
            "decision": "approved",
            "review_due_at": (datetime.now(UTC).date() + timedelta(days=7)).isoformat(),
            "reason": "미확인 이용 조건의 공개 차단을 검증합니다.",
        },
    )
    assert decision.status_code == 422
    with test_app.state.session_factory() as db:
        assert active_catalog(db) is None
        assert list(db.scalars(select(ServiceCatalogReview.decision))) == ["staged"]


def test_pilot_does_not_turn_conflicting_departments_or_question_drafts_into_facts(
    client: TestClient,
) -> None:
    bundle = ServiceBundle.model_validate(
        client.get("/api/v1/service-catalogs/candidates/seongnam-pilot").json()
    )
    services = {item.id: item for item in bundle.services}
    documents = {item.id: item for item in bundle.documents}
    for key, original_code in [("basic-pension", "22006"), ("activity-support", "23002")]:
        service = services[f"pilot-service-{key}"]
        assert service.source_code == original_code
        assert service.work_assignment_ids == []
        assert "하단 담당 부서" in documents[service.source_document_id].text
    assert all(service.requires_human_review for service in bundle.services)
    assert all(
        service.effective_from is None and service.effective_until is None
        for service in bundle.services
    )
    assert all(
        not field.required for service in bundle.services for field in service.required_information
    )
    welfare = [item for item in bundle.services if item.source_code]
    assert len(welfare) == 4
    assert not any(
        field.field_id in {"resident_number", "account_number", "diagnosis", "document_photo"}
        for service in welfare
        for field in service.required_information
    )
