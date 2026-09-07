from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import ServiceCatalogVersion
from app.service_data_schemas import ServiceBundle


def test_review_page_requires_login_and_bundled_candidates_do_not_import(
    test_app: FastAPI, anonymous_client: TestClient, client: TestClient
) -> None:
    assert (
        anonymous_client.get("/staff/service-catalogs", follow_redirects=False).status_code == 303
    )
    result = client.get("/api/v1/service-catalogs/candidates/seongnam")
    assert result.status_code == 200
    bundle = ServiceBundle.model_validate(result.json())
    assert len(bundle.services) == 12 and len(bundle.organizations) == 17
    assert all(not doc.synthetic and doc.retrieval_use == "unknown" for doc in bundle.documents)
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(ServiceCatalogVersion.version))) == 0
    assert client.get("/api/v1/service-catalogs/candidates/unknown").status_code == 404


def test_official_candidate_shows_sources_and_cannot_be_published_without_usage_review(
    client: TestClient,
) -> None:
    bundle = client.get("/api/v1/service-catalogs/candidates/seongnam").json()
    imported = client.post("/api/v1/service-catalogs", json=bundle)
    assert imported.status_code == 201
    page = client.get(f"/staff/service-catalogs/{bundle['version']}")
    assert page.status_code == 200
    assert 'value="approved" disabled' in page.text
    assert "소통관 &gt; 소통지원팀" in page.text
    assert "원본 HTML 추출물이 아니며" in page.text
    decision = client.post(
        f"/api/v1/service-catalogs/{bundle['version']}/review",
        json={
            "content_hash": imported.json()["content_hash"],
            "decision": "approved",
            "review_due_at": "2026-09-20",
            "reason": "이용 조건 미확인 자료를 시험합니다.",
        },
    )
    assert decision.status_code == 422


def test_auditor_can_read_but_has_no_import_or_approval_controls(
    anonymous_client: TestClient,
) -> None:
    anonymous_client.post("/login", data={"username": "audit.demo", "password": "audit-demo-2026"})
    page = anonymous_client.get("/staff/service-catalogs")
    assert page.status_code == 200
    assert "data-catalog-import" not in page.text and "data-catalog-review" not in page.text
    assert "안내 자료 검수" in page.text
