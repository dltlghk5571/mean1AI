import re
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    AuditEvent,
    CitizenSession,
    CitizenSubmission,
    Complaint,
    PublishedReply,
)
from app.services import citizen

PHONE = "010-1111-2222"  # Synthetic identifiers only.


def start(client: TestClient) -> dict[str, str]:
    page = client.get("/minwon/new")
    assert page.status_code == 200
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    key = re.search(r'name="request_key" value="([^"]+)"', page.text)
    assert csrf and key
    client.headers["X-Citizen-CSRF"] = csrf[1]
    return {
        "title": f"합성 가로등 민원 {PHONE}",
        "content": f"가상 데모공원 가로등이 꺼졌습니다. 합성 연락처 {PHONE}",
        "location_text": f"가상 데모공원 {PHONE}",
        "consent": "yes",
        "request_key": key[1],
    }


def submit(client: TestClient, data: dict[str, str] | None = None) -> tuple[str, str, str]:
    response = client.post("/minwon/submit", json=data or start(client))
    assert response.status_code == 200, response.text
    path = response.json()["redirect"]
    receipt = client.get(path)
    number = re.search(r"data-receipt-number>([^<]+)</code>", receipt.text)
    code = re.search(r"data-lookup-code>([^<]+)</code>", receipt.text)
    assert number and code
    return path.split("/")[2], number[1], code[1]


def approve(
    client: TestClient, complaint_id: str, answer: str = "담당자가 확인한 합성 답변입니다."
) -> int:
    response = client.post(
        f"/api/v1/complaints/{complaint_id}/approve",
        json={"department_id": "ROAD_LIGHTING", "answer_draft": answer},
    )
    assert response.status_code == 200, response.text
    reviews = client.get(f"/api/v1/complaints/{complaint_id}/reviews").json()
    return reviews[-1]["id"]


def publish(client: TestClient, complaint_id: str, review_id: int):
    return client.post(
        f"/complaints/{complaint_id}/publish",
        data={"review_id": str(review_id), "confirm_publication": "yes"},
        follow_redirects=False,
    )


def test_public_home_and_staff_boundary(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/")
    assert response.status_code == 200
    assert "우리 동네 불편" in response.text
    assert 'href="/minwon/new"' in response.text
    assert "data-queue-item" not in response.text
    for path in ("/staff", "/complaints/private", "/submit"):
        method = anonymous_client.post if path == "/submit" else anonymous_client.get
        assert method(path, follow_redirects=False).status_code == 303
    assert anonymous_client.get("/api/v1/complaints").status_code == 401


def test_preview_is_redacted_without_saving_or_running_pipeline(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = start(anonymous_client)

    def forbidden(*args, **kwargs):
        pytest.fail("Preview must not run classification or save a complaint")

    monkeypatch.setattr(test_app.state.pipeline, "create_and_process", forbidden)
    response = anonymous_client.post("/minwon/preview", json=data)
    assert response.status_code == 200
    assert PHONE not in response.text
    assert "[전화번호]" in response.json()["title"]
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0
        assert db.scalar(select(func.count(AuditEvent.id))) == 0


def test_intake_receipt_and_private_allowlist(client: TestClient, test_app: FastAPI) -> None:
    data = start(client)
    data["title"] += ' <script>alert("x")</script>'
    complaint_id, number, code = submit(client, data)
    for path in (f"/minwon/{complaint_id}", f"/minwon/{complaint_id}/receipt", "/minwon/lookup"):
        response = client.get(path)
        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["referrer-policy"] == "no-referrer"
        assert PHONE not in response.text
        assert '<script>alert("x")</script>' not in response.text
        for private in ("routing_confidence", "ROAD_LIGHTING", "review.demo", "감사 로그"):
            assert private not in response.text
    assert code not in client.get(f"/minwon/{complaint_id}").text
    assert number in client.get("/minwon/lookup").text
    assert "확인 중" in client.get(f"/minwon/{complaint_id}").text
    with test_app.state.session_factory() as db:
        submission = db.get(CitizenSubmission, complaint_id)
        assert submission.lookup_code_hash == citizen.code_digest(code)
        assert code not in repr(submission.__dict__)
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "citizen_access_created"))
        assert event.details == {"access": "private", "demo_consent": True}
        assert number not in str(event.details) and code not in str(event.details)
    assert "시민에게 답변 공개" in client.get(f"/complaints/{complaint_id}").text
    assert "[전화번호]" in client.get("/staff").text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", " a "),
        ("content", " a "),
        ("location_text", "가" * 301),
        ("consent", ""),
        ("request_key", "not-a-uuid"),
    ],
)
def test_server_validation_does_not_save_invalid_input(
    anonymous_client: TestClient, test_app: FastAPI, field: str, value: str
) -> None:
    data = start(anonymous_client)
    data[field] = value
    response = anonymous_client.post("/minwon/submit", json=data)
    assert response.status_code == 422
    assert field in response.json()["errors"]
    assert PHONE not in response.text
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0


@pytest.mark.parametrize("path", ["/minwon/preview", "/minwon/submit", "/minwon/lookup"])
def test_citizen_posts_require_their_own_session_and_csrf(client: TestClient, path: str) -> None:
    assert client.post(path, json={}).status_code == 403  # Officer cookie/CSRF is insufficient.
    data = start(client)
    client.headers["X-Citizen-CSRF"] = "wrong"
    assert client.post(path, json=data).status_code == 403
    client.cookies.delete(citizen.COOKIE_NAME)
    assert client.post(path, json=data).status_code == 403


def test_private_lookup_grants_only_one_record(client: TestClient, test_app: FastAPI) -> None:
    first_id, number, code = submit(client)
    second_id, _, _ = submit(client)
    with TestClient(test_app) as visitor:
        start(visitor)
        for path in (f"/minwon/{first_id}", f"/minwon/{first_id}/receipt", "/minwon/missing"):
            assert visitor.get(path).status_code == 404
        wrong_code = visitor.post(
            "/minwon/lookup", json={"receipt_number": number, "lookup_code": "wrong"}
        )
        wrong_number = visitor.post(
            "/minwon/lookup", json={"receipt_number": "SN-unknown", "lookup_code": code}
        )
        assert wrong_number.json() == wrong_code.json()
        success = visitor.post(
            "/minwon/lookup",
            json={"receipt_number": number.lower(), "lookup_code": code.lower().replace("-", " ")},
        )
        assert success.json()["redirect"] == f"/minwon/{first_id}"
        assert code not in success.text
        assert visitor.get(success.json()["redirect"]).status_code == 200
        assert visitor.get(f"/minwon/{second_id}").status_code == 404
        assert code not in visitor.get(f"/minwon/{first_id}/receipt").text
        listing = visitor.get("/minwon/lookup").text
        assert first_id in listing and second_id not in listing


def test_expired_session_requires_receipt_code_again(client: TestClient, test_app: FastAPI) -> None:
    complaint_id, number, code = submit(client)
    token = client.cookies[citizen.COOKIE_NAME]
    with test_app.state.session_factory() as db:
        session = db.get(CitizenSession, citizen.digest(token))
        session.expires_at = int(time.time()) - 1
        db.commit()
    assert client.get(f"/minwon/{complaint_id}").status_code == 404
    assert client.post("/minwon/submit", json={}).status_code == 403
    start(client)
    assert client.cookies[citizen.COOKIE_NAME] != token
    response = client.post("/minwon/lookup", json={"receipt_number": number, "lookup_code": code})
    assert response.status_code == 200
    assert client.get(response.json()["redirect"]).status_code == 200


def test_intake_retry_and_concurrent_requests_save_once(
    client: TestClient, test_app: FastAPI
) -> None:
    data = start(client)
    with ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="citizen-idempotency-test"
    ) as executor:
        responses = list(executor.map(lambda _: client.post("/minwon/submit", json=data), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    retry = client.post("/minwon/submit", json=data)
    assert retry.json() == responses[0].json()
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 1
        assert db.scalar(select(func.count(CitizenSubmission.complaint_id))) == 1
        assert (
            db.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.action == "complaint_received")
            )
            == 1
        )


def test_intake_failure_rolls_back_complaint_ownership_and_audit(
    client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    data = start(client)

    def fail_audit(*args, **kwargs):
        raise RuntimeError(f"synthetic private error: {PHONE}")

    with monkeypatch.context() as patch:
        patch.setattr(citizen, "record_audit", fail_audit)
        response = client.post("/minwon/submit", json=data)
    assert response.status_code == 503
    assert PHONE not in response.text and PHONE not in caplog.text
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0
        assert db.scalar(select(func.count(CitizenSubmission.complaint_id))) == 0
        assert db.scalar(select(func.count(AuditEvent.id))) == 0
    assert client.post("/minwon/submit", json=data).status_code == 200


def test_lookup_rate_limit_cannot_be_reset_with_new_cookie(client: TestClient) -> None:
    start(client)
    for _ in range(10):
        assert client.post("/minwon/lookup", json={}).status_code == 400
    client.cookies.delete(citizen.COOKIE_NAME)
    start(client)
    response = client.post("/minwon/lookup", json={})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_oversized_body_and_invalid_json_are_safe(client: TestClient) -> None:
    start(client)
    assert client.post("/minwon/submit", content="x" * 160_001).status_code == 413
    response = client.post("/minwon/submit", content=f"invalid: {PHONE}")
    assert response.status_code == 400
    assert PHONE not in response.text


def test_internal_approval_stays_private_until_explicit_publication(
    client: TestClient, test_app: FastAPI
) -> None:
    complaint_id, _, _ = submit(client)
    answer = "공개 검증용 합성 답변: 현장 확인이 필요합니다."
    review_id = approve(client, complaint_id, answer)
    detail = client.get(f"/minwon/{complaint_id}")
    assert answer not in detail.text
    assert "내용을 확인하고 있어요" in detail.text
    assert (
        client.post(
            f"/complaints/{complaint_id}/publish", data={"review_id": str(review_id)}
        ).status_code
        == 400
    )
    assert publish(client, complaint_id, review_id).status_code == 303
    assert publish(client, complaint_id, review_id).status_code == 303
    detail = client.get(f"/minwon/{complaint_id}")
    assert answer in detail.text
    assert "review.demo" not in detail.text
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(PublishedReply.id))) == 1
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "citizen_reply_published"))
        assert event.actor_id == "review.demo"
        assert event.details["external_message_sent"] is False
        assert answer not in str(event.details)
        with pytest.raises(IntegrityError, match="append-only"):
            db.execute(text("UPDATE published_replies SET answer_text = 'changed'"))
        db.rollback()
        with pytest.raises(IntegrityError, match="append-only"):
            db.execute(text("DELETE FROM published_replies"))


def test_published_snapshot_survives_edits_and_reprocessing(client: TestClient) -> None:
    complaint_id, _, _ = submit(client)
    old_answer = "합성 첫 번째 공개 답변입니다."
    old_review = approve(client, complaint_id, old_answer)
    publish(client, complaint_id, old_review)
    new_answer = "합성 두 번째 검토 초안은 아직 비공개입니다."
    new_review = approve(client, complaint_id, new_answer)
    detail = client.get(f"/minwon/{complaint_id}").text
    assert old_answer in detail and new_answer not in detail
    assert "publication=stale" in publish(client, complaint_id, old_review).headers["location"]
    response = client.post(f"/complaints/{complaint_id}/reprocess", follow_redirects=False)
    assert response.status_code == 303
    assert "publication=stale" in publish(client, complaint_id, new_review).headers["location"]
    assert old_answer in client.get(f"/minwon/{complaint_id}").text
    newest_review = approve(client, complaint_id, new_answer)
    assert publish(client, complaint_id, newest_review).status_code == 303
    detail = client.get(f"/minwon/{complaint_id}").text
    assert new_answer in detail and old_answer not in detail


@pytest.mark.parametrize("role", ["triage", "audit"])
def test_publication_requires_reviewer_role(client: TestClient, role: str) -> None:
    complaint_id, _, _ = submit(client)
    review_id = approve(client, complaint_id)
    client.post("/login", data={"username": f"{role}.demo", "password": f"{role}-demo-2026"})
    client.headers["X-CSRF-Token"] = client.get("/api/v1/session").json()["csrf_token"]
    assert publish(client, complaint_id, review_id).status_code == 403


def test_publication_requires_staff_csrf_and_current_review(client: TestClient) -> None:
    complaint_id, _, _ = submit(client)
    review_id = approve(client, complaint_id)
    csrf = client.headers.pop("X-CSRF-Token")
    assert publish(client, complaint_id, review_id).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert "publication=stale" in publish(client, complaint_id, review_id + 1).headers["location"]
    assert client.get(f"/minwon/{complaint_id}").text.count("담당자가 확인한 합성 답변입니다.") == 0


@pytest.mark.parametrize(
    ("title", "content", "status"),
    [
        ("합성 가스 누출", "가상 배관에서 가스 냄새가 심하고 누출됩니다.", "urgent_review"),
        ("합성 복지 자격 문의", "기초생활 수급 자격 대상인지 확인 부탁합니다.", "needs_review"),
    ],
)
def test_citizen_intake_preserves_mandatory_human_review(
    client: TestClient, title: str, content: str, status: str
) -> None:
    data = start(client)
    data.update(title=title, content=content, request_key=str(uuid4()))
    complaint_id, _, _ = submit(client, data)
    internal = client.get(f"/api/v1/complaints/{complaint_id}").json()
    assert internal["status"] == status
    assert internal["requires_human_review"] is True
    assert internal["assigned_department_id"] is None
