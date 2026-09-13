import json
import re
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.chat_schemas import AgentContext, AgentReply
from app.models import AuditEvent, CitizenChat, CitizenChatAuditEvent, CitizenSubmission, Complaint
from app.services.chat_provider import DemoChatProvider, UnavailableChatProvider

PHONE = "010-1111-2222"  # Synthetic identifiers only.
DESCRIPTION = "가상 데모공원 가로등이 어제부터 꺼져 있어요."


def start(client: TestClient) -> dict:
    page = client.get("/minwon/new")
    assert page.status_code == 200
    assert "시연 모드" in page.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf
    client.headers["X-Citizen-CSRF"] = csrf[1]
    response = client.post("/minwon/chat/open", json={})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def payload(state: dict, action: str, **values: str) -> dict[str, str]:
    return {
        "revision": str(state["revision"]),
        "request_id": str(uuid4()),
        "action": action,
        **values,
    }


def turn(client: TestClient, state: dict, action: str, **values: str) -> dict:
    response = client.post("/minwon/chat/turn", json=payload(state, action, **values))
    assert response.status_code == 200, response.text
    return response.json()


def review(client: TestClient) -> dict:
    state = turn(client, start(client), "say", message=DESCRIPTION)
    assert state["stage"] == "intent"
    state = turn(client, state, "complaint")
    assert state["stage"] == "location"
    return turn(client, state, "say", message="가상 데모공원 정문 앞")


def test_chat_to_private_receipt_and_server_owned_draft(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    state = review(anonymous_client)
    assert state["stage"] == "review"
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0
        assert db.scalar(select(func.count(CitizenChatAuditEvent.id))) == 4
    state = turn(
        anonymous_client,
        state,
        "edit",
        title="가상 공원 조명",
        content=DESCRIPTION,
        location_text="가상 데모공원 서문",
    )
    result = turn(anonymous_client, state, "confirm", consent="yes")
    assert result["stage"] == "submitted"
    receipt = anonymous_client.get(result["redirect"])
    assert receipt.status_code == 200
    assert "가상 공원 조명" in receipt.text
    assert "data-lookup-code" in receipt.text
    with test_app.state.session_factory() as db:
        complaint = db.scalar(select(Complaint))
        assert complaint and complaint.location_text == "가상 데모공원 서문"
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "citizen_chat_confirmed"))
        assert event and event.details["demo_consent"] is True
        assert event.complaint_id == complaint.id


def test_chat_resume_private_session_and_no_internal_fields(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    state = review(anonymous_client)
    assert start(anonymous_client) == state
    serialized = json.dumps(state)
    for hidden in ("owner_session_hash", "submission_key", "csrf_token", "classifier_evidence"):
        assert hidden not in serialized
    with TestClient(test_app) as other:
        other_state = start(other)
        assert other_state["stage"] == "welcome"
        assert DESCRIPTION not in str(other_state)
        assert (
            other.post(
                "/minwon/chat/turn", json=payload(state, "confirm", consent="yes")
            ).status_code
            == 409
        )


def test_only_citizen_csrf_authorizes_chat(client: TestClient) -> None:
    assert client.post("/minwon/chat/open", json={}).status_code == 403
    state = start(client)
    client.headers["X-Citizen-CSRF"] = "invalid"
    response = client.post("/minwon/chat/turn", json=payload(state, "say", message=DESCRIPTION))
    assert response.status_code == 403


def test_redacted_before_provider_storage_and_audit(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    contexts: list[AgentContext] = []

    class SpyProvider(DemoChatProvider):
        def respond(self, context: AgentContext) -> AgentReply:
            contexts.append(context)
            assert PHONE not in context.model_dump_json()
            assert "owner_session" not in context.model_dump_json()
            reply = super().respond(context)
            reply.message += f" 합성 예시 {PHONE}"
            # Provider context is isolated from server-owned draft state.
            context.state.draft.content = "provider attempted to replace the complaint"
            return reply

    test_app.state.chat_provider = SpyProvider()
    state = turn(anonymous_client, start(anonymous_client), "say", message=f"{DESCRIPTION} {PHONE}")
    assert contexts and PHONE not in json.dumps(state)
    assert state["draft"]["content"] == f"{DESCRIPTION} [전화번호]"
    with test_app.state.session_factory() as db:
        chat = db.scalar(select(CitizenChat))
        assert chat and PHONE not in json.dumps(chat.state)
        for event in db.scalars(select(CitizenChatAuditEvent)):
            serialized = json.dumps(event.details)
            assert PHONE not in serialized
            assert DESCRIPTION not in serialized


def test_information_does_not_create_complaint(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    state = turn(anonymous_client, start(anonymous_client), "information")
    assert state["stage"] == "information"
    assert "연결되지 않았어요" in state["messages"][-1]["text"]
    assert state["sources"][0]["url"] == "https://www.bokjiro.go.kr/ssis-tbu/"
    denied = anonymous_client.post(
        "/minwon/chat/turn", json=payload(state, "confirm", consent="yes")
    )
    assert denied.status_code == 422
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0


def test_explicit_start_description_and_optional_location(anonymous_client: TestClient) -> None:
    state = turn(anonymous_client, start(anonymous_client), "complaint")
    assert state["stage"] == "description"
    state = turn(anonymous_client, state, "say", message=DESCRIPTION)
    state = turn(anonymous_client, state, "skip_location")
    assert state["stage"] == "review" and state["draft"]["location_text"] == ""


def test_consent_and_final_payload_tampering_are_rejected(anonymous_client: TestClient) -> None:
    state = review(anonymous_client)
    for values in ({}, {"consent": "yes", "content": "조작한 다른 내용입니다."}):
        response = anonymous_client.post(
            "/minwon/chat/turn", json=payload(state, "confirm", **values)
        )
        assert response.status_code == 422
    assert start(anonymous_client) == state


def test_retry_is_bound_to_request_body_and_stale_tabs_conflict(
    anonymous_client: TestClient,
) -> None:
    state = start(anonymous_client)
    request = payload(state, "say", message=DESCRIPTION)
    first = anonymous_client.post("/minwon/chat/turn", json=request)
    assert first.status_code == 200
    assert anonymous_client.post("/minwon/chat/turn", json=request).json() == first.json()
    request["message"] = "가상 공원 쓰레기를 치워 주세요."
    assert anonymous_client.post("/minwon/chat/turn", json=request).status_code == 409
    assert (
        anonymous_client.post("/minwon/chat/turn", json=payload(state, "information")).status_code
        == 409
    )


def test_concurrent_confirmation_creates_exactly_one_receipt(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    state = review(anonymous_client)
    request = payload(state, "confirm", consent="yes")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="chat-confirm-test") as pool:
        responses = list(
            pool.map(lambda _: anonymous_client.post("/minwon/chat/turn", json=request), range(2))
        )
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 1
        assert db.scalar(select(func.count(CitizenSubmission.complaint_id))) == 1
        assert (
            db.scalar(
                select(func.count(CitizenChatAuditEvent.id)).where(
                    CitizenChatAuditEvent.action == "citizen_confirmed"
                )
            )
            == 1
        )


def test_failed_intake_rolls_back_draft_receipt_and_all_approval_events(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = review(anonymous_client)
    original = test_app.state.pipeline.create_and_process

    def fail_after_pipeline(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError(f"synthetic confidential value {PHONE}")

    monkeypatch.setattr(test_app.state.pipeline, "create_and_process", fail_after_pipeline)
    request = payload(state, "confirm", consent="yes")
    response = anonymous_client.post("/minwon/chat/turn", json=request)
    assert response.status_code == 503 and PHONE not in response.text
    assert start(anonymous_client) == state
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0
        assert db.scalar(select(func.count(AuditEvent.id))) == 0
    monkeypatch.setattr(test_app.state.pipeline, "create_and_process", original)
    assert anonymous_client.post("/minwon/chat/turn", json=request).status_code == 200


def test_provider_failure_preserves_state_and_emergency_notice(
    anonymous_client: TestClient, test_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    state = start(anonymous_client)
    test_app.state.chat_provider = UnavailableChatProvider()
    request = payload(state, "say", message=f"합성 시나리오: 화재가 났어요. {PHONE}")
    response = anonymous_client.post("/minwon/chat/turn", json=request)
    assert response.status_code == 503 and response.json()["urgent"] is True
    assert start(anonymous_client) == state
    assert PHONE not in response.text + caplog.text
    with test_app.state.session_factory() as db:
        event = db.scalar(
            select(CitizenChatAuditEvent).where(CitizenChatAuditEvent.action == "provider_failed")
        )
        assert event and event.details["urgent"] is True
    test_app.state.chat_provider = DemoChatProvider()
    assert anonymous_client.post("/minwon/chat/turn", json=request).json()["urgent"] is True


@pytest.mark.parametrize("invalid", ["transition", "source", "extra"])
def test_invalid_provider_output_cannot_advance_or_submit(
    invalid: str, anonymous_client: TestClient, test_app: FastAPI
) -> None:
    class InvalidProvider(DemoChatProvider):
        def respond(self, context: AgentContext) -> AgentReply:
            reply = super().respond(context)
            if invalid == "transition":
                return reply.model_copy(update={"next_stage": "submitted"})
            if invalid == "source":
                return reply.model_copy(update={"source_ids": ["unapproved_source"]})
            return AgentReply.model_validate({**reply.model_dump(), "submit": True})

    state = start(anonymous_client)
    test_app.state.chat_provider = InvalidProvider()
    response = anonymous_client.post(
        "/minwon/chat/turn", json=payload(state, "say", message=DESCRIPTION)
    )
    assert response.status_code == 503
    assert start(anonymous_client) == state


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE citizen_chat_audit_events SET action='changed'",
        "DELETE FROM citizen_chat_audit_events",
    ],
)
def test_chat_audit_is_append_only(
    mutation: str, anonymous_client: TestClient, test_app: FastAPI
) -> None:
    start(anonymous_client)
    with test_app.state.session_factory() as db:
        with pytest.raises(IntegrityError, match="append-only"):
            db.execute(text(mutation))
        db.rollback()


def test_reset_invalidates_old_confirmation_and_preserves_existing_receipt(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    state = review(anonymous_client)
    old_request = payload(state, "confirm", consent="yes")
    reset = turn(anonymous_client, state, "reset")
    assert reset["stage"] == "welcome" and reset["draft"]["content"] == ""
    assert anonymous_client.post("/minwon/chat/turn", json=old_request).status_code == 409
    result = turn(anonymous_client, review(anonymous_client), "confirm", consent="yes")
    turn(anonymous_client, result, "reset")
    assert anonymous_client.get(result["redirect"]).status_code == 200
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"unexpected": "value"},
        {"revision": "-1"},
        {"revision": 0},
        {"request_id": "bad"},
        {"message": "x" * 4001},
        {"action": "execute_tool"},
    ],
)
def test_invalid_client_envelopes_do_not_change_state(
    changes: dict, anonymous_client: TestClient
) -> None:
    state = start(anonymous_client)
    request = {**payload(state, "say", message=DESCRIPTION), **changes}
    assert anonymous_client.post("/minwon/chat/turn", json=request).status_code in {400, 422}
    assert start(anonymous_client) == state
