import json
import re
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    AuditEvent,
    CitizenFollowUp,
    CitizenFollowUpReply,
    CitizenSession,
    Complaint,
)
from app.services import citizen, citizen_followups

PHONE = "010-1111-2222"  # Synthetic only.


def intake(client: TestClient) -> str:
    page = client.get("/minwon/form")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    key = re.search(r'name="request_key" value="([^"]+)"', page.text)
    assert csrf and key
    client.headers["X-Citizen-CSRF"] = csrf[1]
    response = client.post(
        "/minwon/submit",
        json={
            "title": "합성 공원 가로등",
            "content": "가상 데모공원 가로등이 꺼졌어요.",
            "location_text": "가상 데모공원",
            "request_key": key[1],
            "consent": "yes",
        },
    )
    assert response.status_code == 200
    return response.json()["redirect"].split("/")[2]


def add(client: TestClient, complaint_id: str, **updates):
    return client.post(
        f"/minwon/{complaint_id}/follow-ups",
        json={
            "body": "합성 문의: 확인 일정이 궁금합니다.",
            "request_key": str(uuid4()),
            "confirmed": "yes",
            **updates,
        },
    )


def followup_id(response) -> str:
    assert response.status_code == 200, response.text
    return response.json()["redirect"].split("#followup-")[1]


def reply(client: TestClient, complaint_id: str, item_id: str, **updates):
    return client.post(
        f"/complaints/{complaint_id}/follow-ups/{item_id}/reply",
        json={
            "body": "합성 답변: 현장 확인 일정을 검토하고 있습니다.",
            "confirmed": "yes",
            **updates,
        },
    )


def test_followup_and_public_reply_without_new_complaint_or_model(
    client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    complaint_id = intake(client)
    with test_app.state.session_factory() as db:
        complaint = db.get(Complaint, complaint_id)
        complaint.status = "reviewed"
        complaint.answer_draft = "시민에게 공개하지 않은 내부 초안"
        db.commit()

    def forbidden(*args, **kwargs):
        pytest.fail("Follow-up must not invoke complaint/model processing")

    monkeypatch.setattr(test_app.state.pipeline, "create_and_process", forbidden)
    monkeypatch.setattr(test_app.state.pipeline, "reprocess", forbidden)
    result = add(client, complaint_id)
    item_id = followup_id(result)
    citizen_page = client.get(result.json()["redirect"])
    assert citizen_page.status_code == 200
    assert "합성 문의: 확인 일정이 궁금합니다." in citizen_page.text
    assert "시민에게 공개하지 않은 내부 초안" not in citizen_page.text
    assert "no-store" in citizen_page.headers["cache-control"]
    assert item_id in client.get("/staff/follow-ups").text
    assert item_id in client.get(f"/complaints/{complaint_id}").text
    assert reply(client, complaint_id, item_id).status_code == 200
    assert item_id not in client.get("/staff/follow-ups").text
    page = client.get(f"/minwon/{complaint_id}")
    assert "합성 답변: 현장 확인 일정을 검토하고 있습니다." in page.text
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Complaint)) == 1
        assert db.get(Complaint, complaint_id).status == "reviewed"
        actions = list(db.scalars(select(AuditEvent.action)))
        assert actions.count("citizen_followup_added") == 1
        assert actions.count("citizen_followup_reply_published") == 1


def test_private_access_grants_are_read_only_and_sessions_expire(
    client: TestClient, test_app: FastAPI
) -> None:
    complaint_id = intake(client)
    item_id = followup_id(add(client, complaint_id))
    receipt = client.get(f"/minwon/{complaint_id}/receipt")
    number = re.search(r"data-receipt-number>([^<]+)</code>", receipt.text)
    code = re.search(r"data-lookup-code>([^<]+)</code>", receipt.text)
    assert number and code
    with TestClient(test_app) as stranger:
        assert add(stranger, complaint_id).status_code == 403
        page = stranger.get("/minwon/lookup")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        assert csrf
        stranger.headers["X-Citizen-CSRF"] = csrf[1]
        assert stranger.get(f"/minwon/{complaint_id}").status_code == 404
        assert add(stranger, complaint_id).status_code == 404
        lookup = stranger.post(
            "/minwon/lookup", json={"receipt_number": number[1], "lookup_code": code[1]}
        )
        assert lookup.status_code == 200
        page = stranger.get(lookup.json()["redirect"])
        assert item_id in page.text and "data-followup-form" not in page.text
        assert add(stranger, complaint_id).status_code == 404
    with test_app.state.session_factory() as db:
        session = citizen.read_session(db, client.cookies.get(citizen.COOKIE_NAME))
        assert session is not None
        session.expires_at = 0
        db.commit()
    assert add(client, complaint_id).status_code == 403
    assert client.get(f"/minwon/{complaint_id}").status_code == 404


@pytest.mark.parametrize(
    "body,urgent",
    [
        (f"합성 연락처 {PHONE} sample@example.test 가스 누출이 있어요.", True),
        ("합성 안전 평가: 죽고 싶다는 표현이 추가됨", True),
        ("합성 상담: 기초생활 지원 대상인지 궁금해요.", False),
    ],
)
def test_redaction_safety_audit_and_no_automatic_status_change(
    client: TestClient, test_app: FastAPI, body: str, urgent: bool
) -> None:
    complaint_id = intake(client)
    with test_app.state.session_factory() as db:
        before = db.get(Complaint, complaint_id).status
    item_id = followup_id(add(client, complaint_id, body=body))
    with test_app.state.session_factory() as db:
        item = db.get(CitizenFollowUp, item_id)
        assert (item.urgency != "normal") is urgent
        assert item.review_reasons
        assert PHONE not in item.body and "sample@example.test" not in item.body
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "citizen_followup_added"))
        audit = json.dumps(event.details, ensure_ascii=False)
        assert PHONE not in audit and body not in audit
        assert event.details["requires_human_review"] is True
        assert db.get(Complaint, complaint_id).status == before
    if urgent:
        assert "긴급 안전 확인 필요" in client.get("/staff/follow-ups").text
        assert "위험이 진행 중이면" in client.get(f"/minwon/{complaint_id}").text


def test_validation_csrf_throttling_and_safe_html(client: TestClient) -> None:
    complaint_id = intake(client)
    path = f"/minwon/{complaint_id}/follow-ups"
    data = {"request_key": str(uuid4()), "body": "합성 문의입니다.", "confirmed": "yes"}
    assert client.post(path, json=data, headers={"X-Citizen-CSRF": "wrong"}).status_code == 403
    assert client.post(path, json={**data, "extra": "forbidden"}).status_code == 422
    assert client.post(path, json={**data, "body": " "}).status_code == 422
    assert client.post(path, json={**data, "confirmed": ""}).status_code == 422
    result = add(client, complaint_id, body='<script>alert("synthetic")</script>')
    page = client.get(result.json()["redirect"])
    assert '<script>alert("synthetic")</script>' not in page.text
    assert "&lt;script&gt;" in page.text
    assert client.post(path, content=b"x" * 160_001).status_code == 413
    response = add(client, complaint_id)
    assert response.status_code == 429 and response.headers["retry-after"] == "60"


def test_retry_conflict_and_reply_permission(client: TestClient, test_app: FastAPI) -> None:
    complaint_id = intake(client)
    key = str(uuid4())
    item_id = followup_id(add(client, complaint_id, request_key=key))
    assert followup_id(add(client, complaint_id, request_key=key)) == item_id
    assert (
        add(client, complaint_id, request_key=key, body="합성 수정된 문의입니다.").status_code
        == 409
    )
    assert reply(client, complaint_id, item_id, confirmed="").status_code == 422
    assert reply(client, complaint_id, item_id, body=f"합성 연락처 {PHONE}").status_code == 422
    assert reply(client, "wrong-complaint", item_id).status_code == 404
    for name, password in [("triage.demo", "triage-demo-2026"), ("audit.demo", "audit-demo-2026")]:
        with TestClient(test_app) as other:
            other.post("/login", data={"username": name, "password": password})
            other.headers["X-CSRF-Token"] = other.get("/api/v1/session").json()["csrf_token"]
            assert "답변 작성·공개" not in other.get("/staff/follow-ups").text
            assert reply(other, complaint_id, item_id).status_code == 403
    assert (
        client.post(
            f"/complaints/{complaint_id}/follow-ups/{item_id}/reply",
            json={"body": "합성 답변입니다.", "confirmed": "yes"},
            headers={"X-CSRF-Token": "wrong"},
        ).status_code
        == 403
    )
    assert reply(client, complaint_id, item_id).status_code == 200
    assert reply(client, complaint_id, item_id).status_code == 200
    assert reply(client, complaint_id, item_id, body="합성 다른 답변입니다.").status_code == 409
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(CitizenFollowUpReply)) == 1


def test_atomic_rollback_and_append_only_records(
    client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    complaint_id = intake(client)
    real_audit = citizen_followups.record_audit

    def failed(*args, **kwargs):
        raise RuntimeError("Synthetic persistence error")

    monkeypatch.setattr(citizen_followups, "record_audit", failed)
    assert add(client, complaint_id).status_code == 503
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(CitizenFollowUp)) == 0
    monkeypatch.setattr(citizen_followups, "record_audit", real_audit)
    item_id = followup_id(add(client, complaint_id))
    monkeypatch.setattr(citizen_followups, "record_audit", failed)
    assert reply(client, complaint_id, item_id).status_code == 503
    with test_app.state.session_factory() as db:
        assert db.get(CitizenFollowUpReply, item_id) is None
    monkeypatch.setattr(citizen_followups, "record_audit", real_audit)
    assert reply(client, complaint_id, item_id).status_code == 200
    for table in ("citizen_followups", "citizen_followup_replies"):
        for operation in (f"UPDATE {table} SET body='changed'", f"DELETE FROM {table}"):
            with test_app.state.session_factory() as db, pytest.raises(IntegrityError):
                db.execute(text(operation))


def test_concurrent_retry_and_paginated_pending_queue(
    client: TestClient, test_app: FastAPI
) -> None:
    complaint_id = intake(client)
    with test_app.state.session_factory() as db:
        session = citizen.read_session(db, client.cookies.get(citizen.COOKIE_NAME))
        assert session is not None
        owner = session.token_hash
    key = str(uuid4())

    def send_once() -> str:
        with test_app.state.session_factory() as db:
            return citizen_followups.add_followup(
                db,
                db.get(CitizenSession, owner),
                complaint_id,
                {"request_key": key, "body": "합성 동시 전송 문의", "confirmed": "yes"},
            ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: send_once(), range(2)))
    assert ids[0] == ids[1]
    with test_app.state.session_factory() as db:
        owner_session = db.get(CitizenSession, owner)
        for i in range(21):
            citizen_followups.add_followup(
                db,
                owner_session,
                complaint_id,
                {"request_key": str(uuid4()), "body": f"합성 순서 문의 {i}", "confirmed": "yes"},
            )
        urgent = citizen_followups.add_followup(
            db,
            owner_session,
            complaint_id,
            {"request_key": str(uuid4()), "body": "합성 가스 누출 긴급 문의", "confirmed": "yes"},
        )
        pending = citizen_followups.pending_queue(db)
        assert pending["total"] == 23 and len(pending["items"]) == 20
        assert pending["items"][0]["id"] == urgent.id
        assert citizen_followups.history(db, complaint_id)["items"][0]["id"] == urgent.id
        assert citizen_followups.history(db, complaint_id, 999)["page"] == 2
    assert "다음 페이지" in client.get("/staff/follow-ups").text
    assert "이전 페이지" in client.get("/staff/follow-ups?page=2").text
    assert "이전 내역" in client.get(f"/minwon/{complaint_id}?followup_page=2").text
    assert "이전 내역" in client.get(f"/complaints/{complaint_id}?followup_page=2").text
