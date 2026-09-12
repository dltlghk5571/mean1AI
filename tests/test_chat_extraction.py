"""Synthetic extraction proposals, real application transactions, no listening server."""

import json
from copy import deepcopy
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.extraction_schemas import ExtractionRequest, ExtractionResponse
from app.models import AuditEvent, CitizenChat, CitizenChatAuditEvent, Complaint
from app.services import citizen_chat, citizen_questions
from app.services.chat_extraction import DEMO_TEXT, ExtractionRunner, synthetic_proposal
from tests.test_citizen_chat import payload, start, turn
from tests.test_club_planner import Stream

KEY = "synthetic-extraction-test-key"


def configuration(mode="demo", **overrides) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "chat_extraction_provider": mode,
        "chat_extraction_endpoint_url": "https://models.example.test/v1/agent/extract",
        "chat_model_id": "synthetic-agent-v1",
        "chat_api_key": KEY,
    }
    values.update(overrides)
    return Settings(**values)


def install(test_app: FastAPI, mode="demo", handler=None) -> ExtractionRunner:
    runner = ExtractionRunner(
        configuration(mode), transport=httpx.MockTransport(handler or reply_for)
    )
    test_app.state.extraction_runner = runner
    return runner


def reply_for(request: httpx.Request, mutate=None) -> httpx.Response:
    value = ExtractionRequest.model_validate_json(request.content)
    reply = ExtractionResponse(
        request_id=value.request_id,
        input_hash=value.input_hash,
        model_id=value.model_id,
        execution_mode="model",
        proposal=synthetic_proposal(value),
    ).model_dump(mode="json")
    if mutate:
        mutate(reply)
    return httpx.Response(
        200, headers={"content-type": "application/json"}, stream=Stream(json.dumps(reply).encode())
    )


def proposed(client: TestClient) -> dict:
    return turn(client, start(client), "say", message=DEMO_TEXT)


def test_off_preserves_manual_intake(anonymous_client: TestClient) -> None:
    state = proposed(anonymous_client)
    assert state["stage"] == "intent" and state["extraction_preview"] is None
    assert state["extraction_support"] == {"mode": "off", "example": None}


@pytest.mark.parametrize("mode", ["demo", "club"])
def test_confirmation_preserves_source_skips_known_answer_and_requires_final_consent(
    anonymous_client: TestClient, test_app: FastAPI, mode
) -> None:
    install(test_app, mode)
    state = proposed(anonymous_client)
    preview = state["extraction_preview"]
    assert preview and not preview["stale"] and preview["synthetic"] == (mode == "demo")
    assert state["stage"] == "intent" and state["intake"] is None
    assert state["draft"]["content"] == DEMO_TEXT and state["draft"]["location_text"] == ""
    assert start(anonymous_client) == state
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0
    for action in ("confirm", "complaint", "choose_topic", "say"):
        assert (
            anonymous_client.post(
                "/minwon/chat/turn",
                json=payload(state, action, consent="yes" if action == "confirm" else ""),
            ).status_code
            == 409
        )
    accepted = payload(state, "accept_extraction", extraction_id=preview["id"])
    response = anonymous_client.post("/minwon/chat/turn", json=accepted)
    assert response.status_code == 200, response.text
    state = response.json()
    assert anonymous_client.post("/minwon/chat/turn", json=accepted).json() == state
    assert state["stage"] == "questions" and state["extraction_preview"] is None
    assert state["current_question"]["field_id"] == "facility_label"
    assert state["intake"]["answers"]["observed_time"] == {
        "status": "in_description",
        "value": "어제 저녁부터",
    }
    assert state["draft"]["location_text"] == "합성 별빛공원 동문 앞"
    assert state["submission_content"] == DEMO_TEXT
    state = turn(anonymous_client, state, "finish_questions")
    assert state["stage"] == "review"
    state = turn(
        anonymous_client,
        state,
        "edit",
        title=state["draft"]["title"],
        content=DEMO_TEXT,
        location_text="합성 별빛공원 서문",
    )
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0
    state = turn(anonymous_client, state, "confirm", consent="yes")
    assert state["stage"] == "submitted"
    with test_app.state.session_factory() as db:
        complaint = db.scalar(select(Complaint))
        assert complaint and complaint.location_text == "합성 별빛공원 서문"
        assert complaint.content == DEMO_TEXT
        audits = list(db.scalars(select(CitizenChatAuditEvent)))
        assert [
            event.details["status"] for event in audits if event.action == "extraction_resolved"
        ] == ["proposed", "accepted"]
        assert DEMO_TEXT not in repr([event.details for event in audits])


@pytest.mark.parametrize("change", ["provider", "model", "source", "templates"])
def test_stale_proposal_cannot_be_accepted_but_can_be_dismissed(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch, change
) -> None:
    runner = install(test_app, "club")
    state = proposed(anonymous_client)
    if change == "provider":
        runner.settings.chat_extraction_provider = "off"
    elif change == "model":
        runner.settings.chat_model_id = "synthetic-new-version"
    elif change == "source":
        with test_app.state.session_factory() as db:
            chat = db.scalar(select(CitizenChat))
            assert chat
            snapshot = deepcopy(chat.state)
            snapshot["draft"]["content"] += " 합성 내용 변경"
            chat.state = snapshot
            db.commit()
    else:
        updated = deepcopy(citizen_questions.templates())
        updated["lighting"].version = "synthetic-new-version"
        monkeypatch.setattr(citizen_questions, "templates", lambda: updated)
    latest = start(anonymous_client)
    assert latest["extraction_preview"]["stale"]
    assert latest["extraction_preview"]["rows"] == []
    assert (
        anonymous_client.post(
            "/minwon/chat/turn",
            json=payload(
                latest, "accept_extraction", extraction_id=state["extraction_preview"]["id"]
            ),
        ).status_code
        == 409
    )
    dismissed = turn(
        anonymous_client,
        latest,
        "dismiss_extraction",
        extraction_id=state["extraction_preview"]["id"],
    )
    assert dismissed["stage"] == "intent" and dismissed["intake"] is None
    assert dismissed["draft"]["location_text"] == "" and dismissed["extraction_preview"] is None


def test_proposal_identity_csrf_ownership_and_concurrent_reset(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    install(test_app)
    state = proposed(anonymous_client)
    correct = payload(state, "accept_extraction", extraction_id=state["extraction_preview"]["id"])
    assert (
        anonymous_client.post(
            "/minwon/chat/turn", json={**correct, "extraction_id": str(uuid4())}
        ).status_code
        == 409
    )
    assert (
        anonymous_client.post(
            "/minwon/chat/turn", json={**correct, "location_text": "위조한 장소"}
        ).status_code
        == 422
    )
    with TestClient(test_app) as other:
        assert other.post("/minwon/chat/turn", json=correct).status_code == 403
        other_state = start(other)
        assert (
            other.post(
                "/minwon/chat/turn",
                json=payload(
                    other_state,
                    "accept_extraction",
                    extraction_id=state["extraction_preview"]["id"],
                ),
            ).status_code
            == 409
        )
    reset = turn(anonymous_client, state, "reset")
    assert reset["extraction_preview"] is None
    assert anonymous_client.post("/minwon/chat/turn", json=correct).status_code == 409


def test_failed_and_abstained_extraction_keep_manual_path_and_original_input(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    install(test_app, "club", lambda request: httpx.Response(500, text="synthetic private failure"))
    state = proposed(anonymous_client)
    assert state["extraction_notice"] == "failed" and state["extraction_preview"] is None
    assert state["draft"]["content"] == DEMO_TEXT
    assert "private failure" not in json.dumps(state)
    assert turn(anonymous_client, state, "complaint")["stage"] == "location"
    runner = install(test_app)
    state = turn(anonymous_client, start(anonymous_client), "reset")
    state = turn(
        anonymous_client, state, "say", message="합성 질문입니다. 어느 도움이 필요한지 모르겠어요."
    )
    assert runner.enabled and state["extraction_notice"] == "abstained"
    assert state["extraction_preview"] is None and state["stage"] == "intent"


def test_emergency_bypasses_model_and_keeps_warning(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    def forbidden(request):
        pytest.fail("Urgent input must not wait on extraction")

    install(test_app, "club", forbidden)
    state = turn(
        anonymous_client,
        start(anonymous_client),
        "say",
        message="합성 사고입니다. 공원에서 화재가 발생했어요.",
    )
    assert state["urgent"] and state["extraction_notice"] == "urgent"
    assert state["extraction_preview"] is None


def test_audit_precedes_http_and_competing_turn_discards_proposal(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    initial = start(anonymous_client)
    calls = []

    def handler(request):
        calls.append(request)
        with test_app.state.session_factory() as db:
            assert db.scalar(
                select(CitizenChatAuditEvent).where(
                    CitizenChatAuditEvent.action == "extraction_requested"
                )
            )
        # Another committed request during HTTP proves the original holds no write transaction.
        turn(anonymous_client, initial, "reset")
        return reply_for(request)

    install(test_app, "club", handler)
    response = anonymous_client.post(
        "/minwon/chat/turn", json=payload(initial, "say", message=DEMO_TEXT)
    )
    assert response.status_code == 409 and len(calls) == 1
    assert start(anonymous_client)["stage"] == "welcome"
    with test_app.state.session_factory() as db:
        assert db.scalar(
            select(CitizenChatAuditEvent).where(
                CitizenChatAuditEvent.action == "extraction_aborted"
            )
        )


def test_welfare_information_never_creates_intake_without_later_explicit_complaint(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    source = "합성 복지 지원 정보를 알아보고 싶어요."

    def handler(request):
        def mutate(reply):
            reply["proposal"] = {
                "abstained": False,
                "reason": "supported",
                "purpose": {"value": "information", "quote": "정보를 알아보고 싶어요."},
                "topic": {"template_id": "basic-livelihood", "quote": "복지 지원"},
                "location": None,
                "answers": [],
            }

        return reply_for(request, mutate)

    install(test_app, "club", handler)
    state = turn(anonymous_client, start(anonymous_client), "say", message=source)
    state = turn(
        anonymous_client,
        state,
        "accept_extraction",
        extraction_id=state["extraction_preview"]["id"],
    )
    state = turn(anonymous_client, state, "skip_location")
    state = turn(anonymous_client, state, "finish_questions")
    assert state["stage"] == "information"
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0
    state = turn(anonymous_client, state, "complaint")
    state = turn(anonymous_client, state, "confirm", consent="yes")
    with test_app.state.session_factory() as db:
        complaint = db.scalar(select(Complaint))
        assert complaint and complaint.status == "needs_review"
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "citizen_chat_confirmed"))


@pytest.mark.parametrize("failed_action", ["extraction_requested", "extraction_resolved"])
def test_audit_failure_withholds_proposal_and_preserves_original_revision(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch, failed_action
) -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return reply_for(request)

    install(test_app, "club", handler)
    initial = start(anonymous_client)
    original = citizen_chat.audit

    def fail(db, chat, action, **details):
        if action == failed_action:
            raise RuntimeError("synthetic journal failure")
        return original(db, chat, action, **details)

    monkeypatch.setattr(citizen_chat, "audit", fail)
    response = anonymous_client.post(
        "/minwon/chat/turn", json=payload(initial, "say", message=DEMO_TEXT)
    )
    assert response.status_code == 503
    assert start(anonymous_client) == initial
    assert len(calls) == (0 if failed_action == "extraction_requested" else 1)


def test_editing_source_invalidates_only_extracted_answers(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    install(test_app)
    state = proposed(anonymous_client)
    state = turn(
        anonymous_client,
        state,
        "accept_extraction",
        extraction_id=state["extraction_preview"]["id"],
    )
    state = turn(anonymous_client, state, "finish_questions")
    state = turn(
        anonymous_client,
        state,
        "edit",
        title=state["draft"]["title"],
        content="합성 공원 가로등이 고장 났어요.",
        location_text=state["draft"]["location_text"],
    )
    assert (
        state["stage"] == "questions" and state["current_question"]["field_id"] == "observed_time"
    )
    assert "observed_time" not in state["intake"]["answers"]
    assert state["intake"]["answers"]["facility_label"]["status"] == "skipped"
