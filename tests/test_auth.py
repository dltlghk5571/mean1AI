from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.main import create_app
from app.models import AuditEvent, ReviewDecision
from app.services.auth import SESSION_COOKIE_NAME


def _login(
    client: TestClient,
    *,
    username: str,
    password: str,
    add_csrf_header: bool = True,
) -> dict[str, object]:
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    session = client.get("/api/v1/session")
    assert session.status_code == 200
    body = session.json()
    if add_csrf_header:
        client.headers["X-CSRF-Token"] = str(body["csrf_token"])
    return body


def _create_synthetic_complaint(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/complaints",
        json={
            "title": "합성 인증 경계 가로등 신고",
            "content": "가상 인증시험동 가로등 고장 위치와 관리번호 확인이 필요합니다.",
            "location_text": "가상 인증시험동 1번 위치",
            "channel": "web",
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def triage_client(test_app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(test_app) as test_client:
        _login(
            test_client,
            username="triage.demo",
            password="triage-demo-2026",
        )
        yield test_client


@pytest.fixture
def auditor_client(test_app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(test_app) as test_client:
        _login(
            test_client,
            username="audit.demo",
            password="audit-demo-2026",
        )
        yield test_client


def test_anonymous_requests_are_redirected_or_rejected(
    anonymous_client: TestClient,
) -> None:
    page = anonymous_client.get("/staff?status=review", follow_redirects=False)
    api = anonymous_client.get("/api/v1/complaints")

    assert page.status_code == 303
    assert page.headers["location"].startswith("/login?next=")
    assert api.status_code == 401
    assert api.json() == {"detail": "Authentication required"}
    assert api.headers["www-authenticate"] == "Session"


def test_login_page_uses_synthetic_role_accounts(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/login")

    assert response.status_code == 200
    assert "업무시스템 로그인" in response.text
    assert "분류 담당" in response.text
    assert "검토 승인" in response.text
    assert "감사 조회" in response.text
    assert "실제 계정·시민정보를 입력하지 마세요" in response.text


def test_wrong_password_returns_generic_error_without_echoing_secret(
    anonymous_client: TestClient,
) -> None:
    wrong_password = "synthetic-wrong-secret"
    response = anonymous_client.post(
        "/login",
        data={"username": "review.demo", "password": wrong_password},
    )

    assert response.status_code == 401
    assert "아이디 또는 비밀번호를 확인해 주세요" in response.text
    assert wrong_password not in response.text


def test_session_cookie_is_signed_httponly_and_same_site_strict(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.post(
        "/login",
        data={"username": "review.demo", "password": "review-demo-2026"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie


def test_tampered_session_cookie_is_rejected(anonymous_client: TestClient) -> None:
    anonymous_client.cookies.set(SESSION_COOKIE_NAME, "tampered.payload")

    response = anonymous_client.get("/api/v1/session")

    assert response.status_code == 401


def test_csrf_is_required_for_authenticated_mutations(anonymous_client: TestClient) -> None:
    _login(
        anonymous_client,
        username="review.demo",
        password="review-demo-2026",
        add_csrf_header=False,
    )

    response = anonymous_client.post(
        "/api/v1/complaints",
        json={
            "title": "합성 CSRF 검사",
            "content": "가상 데이터로 CSRF 방어를 확인합니다.",
            "channel": "web",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "invalid_csrf_token"


def test_triage_role_can_process_but_cannot_approve(triage_client: TestClient) -> None:
    complaint = _create_synthetic_complaint(triage_client)
    reprocessed = triage_client.post(f"/api/v1/complaints/{complaint['id']}/reprocess")
    approval = triage_client.post(
        f"/api/v1/complaints/{complaint['id']}/approve",
        json={
            "department_id": "ROAD_LIGHTING",
            "answer_draft": str(complaint["answer_draft"]),
        },
    )

    assert reprocessed.status_code == 200
    assert approval.status_code == 403
    assert approval.json()["detail"] == "insufficient_role"


def test_auditor_role_is_read_only(auditor_client: TestClient) -> None:
    page = auditor_client.get("/staff")
    mutation = auditor_client.post(
        "/api/v1/complaints",
        json={
            "title": "합성 감사 계정 쓰기 시도",
            "content": "감사 조회 역할은 새 민원을 만들 수 없습니다.",
            "channel": "web",
        },
    )

    assert page.status_code == 200
    assert "읽기 전용" in page.text
    assert "data-open-intake" not in page.text
    assert mutation.status_code == 403
    assert mutation.json()["detail"] == "insufficient_role"


def test_actor_identity_comes_from_session_and_review_history_is_append_only(
    client: TestClient,
    test_app: FastAPI,
) -> None:
    complaint = _create_synthetic_complaint(client)
    complaint_id = str(complaint["id"])
    spoofed = client.post(
        f"/api/v1/complaints/{complaint_id}/approve",
        json={
            "department_id": "ROAD_LIGHTING",
            "answer_draft": str(complaint["answer_draft"]),
            "actor_id": "spoofed-admin",
        },
    )
    assert spoofed.status_code == 422

    first = client.post(
        f"/api/v1/complaints/{complaint_id}/approve",
        json={
            "department_id": "ROAD_LIGHTING",
            "answer_draft": str(complaint["answer_draft"]),
        },
    )
    second = client.post(
        f"/api/v1/complaints/{complaint_id}/approve",
        json={
            "department_id": "ROAD_LIGHTING",
            "answer_draft": str(complaint["answer_draft"]) + "\n\n담당자 확인 문구입니다.",
        },
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["reviewed_by"] == "review.demo"

    history = client.get(f"/api/v1/complaints/{complaint_id}/reviews")
    assert history.status_code == 200
    records = history.json()
    assert len(records) == 2
    assert [record["actor_id"] for record in records] == ["review.demo", "review.demo"]
    assert [record["actor_role"] for record in records] == ["reviewer", "reviewer"]
    assert records[0]["id"] < records[1]["id"]
    original_text = records[0]["answer_draft"]

    session_factory = test_app.state.session_factory
    with session_factory() as db:
        decision = db.get(ReviewDecision, records[0]["id"])
        assert decision is not None
        decision.answer_draft = "tampered"
        with pytest.raises(IntegrityError, match="append-only"):
            db.commit()
        db.rollback()

    unchanged = client.get(f"/api/v1/complaints/{complaint_id}/reviews").json()
    assert unchanged[0]["answer_draft"] == original_text

    with session_factory() as db:
        decision = db.get(ReviewDecision, records[0]["id"])
        assert decision is not None
        db.delete(decision)
        with pytest.raises(IntegrityError, match="append-only"):
            db.commit()
        db.rollback()

    with session_factory() as db:
        audit = db.get(AuditEvent, 1)
        assert audit is not None
        audit.action = "tampered"
        with pytest.raises(IntegrityError, match="append-only"):
            db.commit()
        db.rollback()

    with session_factory() as db:
        audit = db.get(AuditEvent, 1)
        assert audit is not None
        db.delete(audit)
        with pytest.raises(IntegrityError, match="append-only"):
            db.commit()
        db.rollback()


def test_direct_identifier_in_human_draft_is_not_saved(client: TestClient) -> None:
    complaint = _create_synthetic_complaint(client)
    synthetic_email = "officer-output@example.com"

    response = client.post(
        f"/api/v1/complaints/{complaint['id']}/approve",
        json={
            "department_id": "ROAD_LIGHTING",
            "answer_draft": f"연락 주소는 {synthetic_email}입니다.",
        },
    )

    assert response.status_code == 400
    assert synthetic_email not in response.text
    assert client.get(f"/api/v1/complaints/{complaint['id']}/reviews").json() == []


def test_detail_page_changes_controls_by_authenticated_role(client: TestClient) -> None:
    complaint = _create_synthetic_complaint(client)
    reviewer_page = client.get(f"/complaints/{complaint['id']}")
    assert reviewer_page.status_code == 200
    assert "검토 기록 계정" in reviewer_page.text
    assert 'id="review-form"' in reviewer_page.text

    session = _login(
        client,
        username="audit.demo",
        password="audit-demo-2026",
    )
    assert session["role"] == "auditor"
    auditor_page = client.get(f"/complaints/{complaint['id']}")

    assert auditor_page.status_code == 200
    assert "검토 승인 권한이 없습니다" in auditor_page.text
    assert 'id="review-form"' not in auditor_page.text
    assert "data-reprocess-form" not in auditor_page.text
    assert "readonly" in auditor_page.text


def test_logout_clears_session(anonymous_client: TestClient) -> None:
    session = _login(
        anonymous_client,
        username="review.demo",
        password="review-demo-2026",
    )
    response = anonymous_client.post(
        "/logout",
        data={"csrf_token": str(session["csrf_token"])},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert anonymous_client.get("/api/v1/session").status_code == 401


def test_login_next_path_rejects_external_redirect(anonymous_client: TestClient) -> None:
    response = anonymous_client.post(
        "/login",
        data={
            "username": "review.demo",
            "password": "review-demo-2026",
            "next_path": "https://malicious.example/redirect",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/staff"


def test_production_mode_refuses_an_ephemeral_session_secret() -> None:
    with pytest.raises(ValueError, match="SESSION_SECRET is required"):
        create_app(
            Settings(
                app_env="production",
                database_url="sqlite:///:memory:",
                ai_provider="rules",
                session_secret=None,
            )
        )
