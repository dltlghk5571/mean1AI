import base64
import io
import json
import re
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.chat_schemas import AgentContext, AgentReply
from app.models import AuditEvent, CitizenChat, CitizenChatAuditEvent, CitizenPhoto, Complaint
from app.services.chat_provider import DemoChatProvider, UnavailableChatProvider
from app.services.citizen_questions import templates

DESCRIPTION = "가상 시연 공원의 가로등이 어제부터 꺼져 있어요."


def start(client: TestClient) -> dict:
    page = client.get("/minwon/new")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf
    client.headers["X-Citizen-CSRF"] = csrf[1]
    result = client.post("/minwon/chat/open", json={})
    assert result.status_code == 200
    return result.json()


def payload(state: dict, action: str, **fields: str) -> dict:
    return {
        "revision": str(state["revision"]),
        "request_id": str(uuid4()),
        "action": action,
        **fields,
    }


def turn(client: TestClient, state: dict, action: str, **fields: str) -> dict:
    response = client.post("/minwon/chat/turn", json=payload(state, action, **fields))
    assert response.status_code == 200, response.text
    return response.json()


def review(client: TestClient, description: str = DESCRIPTION) -> dict:
    state = turn(client, start(client), "say", message=description)
    state = turn(client, state, "complaint")
    return turn(client, state, "skip_location")


@pytest.mark.parametrize("topic_id", list(templates()))
def test_all_topics_reuse_common_fields_and_do_not_submit(
    anonymous_client: TestClient, test_app: FastAPI, topic_id: str
) -> None:
    client = anonymous_client
    state = turn(client, review(client), "choose_topic", template_id=topic_id)
    assert state["stage"] == "questions"
    assert state["current_question"]["field_id"] not in {"content", "location_text"}
    assert state["draft"]["content"] == DESCRIPTION and state["location_checked"]
    state = turn(client, state, "finish_questions")
    assert state["stage"] == (
        "review" if templates()[topic_id].purpose == "complaint" else "information"
    )
    assert state["submission_content"] == DESCRIPTION
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0


def test_topic_first_works_without_provider_and_preserves_answers_on_resume(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    client = anonymous_client
    state = start(client)
    test_app.state.chat_provider = UnavailableChatProvider()
    state = turn(client, state, "choose_topic", template_id="lighting")
    assert state["stage"] == "description"
    state = turn(client, state, "say", message=DESCRIPTION)
    assert state["stage"] == "location"
    state = turn(client, state, "say", message="가상 정문")
    request = payload(state, "already_described", field_id="observed_time")
    first = client.post("/minwon/chat/turn", json=request)
    assert first.status_code == 200
    assert client.post("/minwon/chat/turn", json=request).json() == first.json()
    state = first.json()
    assert state["current_question"]["field_id"] == "facility_label"
    assert start(client) == state
    state = turn(client, state, "skip_question", field_id="facility_label")
    assert state["stage"] == "review"
    state = turn(client, state, "choose_topic", template_id="lighting")
    assert state["stage"] == "review"
    assert state["intake"]["answers"]["observed_time"]["status"] == "in_description"
    state = turn(client, state, "revise_question", field_id="facility_label")
    state = turn(client, state, "answer_question", field_id="facility_label", message="시연 표지 A")
    assert "시설 표지: 시연 표지 A" in state["submission_content"]
    state = turn(client, state, "reset")
    assert state["intake"] is None and not state["location_checked"]


def test_answers_are_redacted_for_lookup_storage_and_confirmed_photo_intake(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    contexts: list[AgentContext] = []

    class Spy(DemoChatProvider):
        def respond(self, context: AgentContext) -> AgentReply:
            contexts.append(context.model_copy(deep=True))
            return super().respond(context)

    client = anonymous_client
    test_app.state.chat_provider = Spy()
    state = turn(client, start(client), "choose_topic", template_id="basic-pension")
    state = turn(client, state, "skip_location")
    state = turn(
        client,
        state,
        "answer_question",
        field_id="request_goal",
        message="연락처 010-1111-2222 신청 방법",
    )
    state = turn(client, state, "skip_question", field_id="application_stage")
    assert state["stage"] == "information" and contexts
    assert "010-1111-2222" not in contexts[-1].model_dump_json()
    assert "신청 방법" in contexts[-1].state.draft.content
    assert "추가로 알려준 내용" not in state["draft"]["content"]
    state = turn(client, state, "complaint")
    assert state["stage"] == "review"
    exact_body = state["submission_content"]
    raw = io.BytesIO()
    with Image.new("RGB", (3, 3), "green") as photo:
        photo.save(raw, format="PNG")
    body = {
        "turn": payload(state, "confirm", consent="yes"),
        "photos": [{"media_type": "image/png", "data": base64.b64encode(raw.getvalue()).decode()}],
    }
    result = client.post("/minwon/chat/confirm-with-photos", json=body)
    assert result.status_code == 200, result.text
    assert client.post("/minwon/chat/confirm-with-photos", json=body).json() == result.json()
    with test_app.state.session_factory() as db:
        complaint = db.scalar(select(Complaint))
        assert complaint and complaint.content == exact_body
        assert db.scalar(select(func.count(CitizenPhoto.id))) == 1
        chat = db.scalar(select(CitizenChat))
        assert chat and "010-1111-2222" not in json.dumps(chat.state)
        journal = [event.details for event in db.scalars(select(CitizenChatAuditEvent))]
        assert "010-1111-2222" not in json.dumps(journal) and "신청 방법" not in json.dumps(
            journal, ensure_ascii=False
        )
        confirmation = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "citizen_chat_confirmed")
        )
        assert confirmation and confirmation.details["template_id"] == "basic-pension"


@pytest.mark.parametrize(
    "fields",
    [
        {"action": "answer_question", "field_id": "facility_label", "message": "없는 질문 답"},
        {"action": "answer_question", "field_id": "observed_time", "message": "가" * 501},
        {"action": "answer_question", "field_id": "observed_time", "message": ""},
        {
            "action": "answer_question",
            "field_id": "observed_time",
            "message": "어제",
            "consent": "yes",
        },
        {"action": "choose_topic", "template_id": "made-up"},
        {"action": "confirm", "consent": "yes"},
        {"action": "say", "message": "우회 답변"},
    ],
)
def test_invalid_question_turn_does_not_change_draft(
    anonymous_client: TestClient, fields: dict
) -> None:
    client = anonymous_client
    state = turn(client, review(client), "choose_topic", template_id="lighting")
    response = client.post("/minwon/chat/turn", json=payload(state, **fields))
    assert response.status_code == 422
    assert client.post("/minwon/chat/open", json={}).json() == state


def test_combined_length_rollback_edit_and_stale_confirmation(anonymous_client: TestClient) -> None:
    client = anonymous_client
    state = turn(client, review(client, "설명" * 1995), "choose_topic", template_id="lighting")
    response = client.post(
        "/minwon/chat/turn",
        json=payload(state, "answer_question", field_id="observed_time", message="어제 저녁"),
    )
    assert response.status_code == 422 and "4,000" in response.text
    assert client.post("/minwon/chat/open", json={}).json() == state
    state = turn(client, state, "finish_questions")
    state = turn(
        client, state, "edit", title="가상 조명", content=DESCRIPTION, location_text="가상 정문"
    )
    previous = state
    state = turn(client, state, "revise_question", field_id="observed_time")
    state = turn(
        client, state, "answer_question", field_id="observed_time", message="지난주 밤부터"
    )
    response = client.post("/minwon/chat/turn", json=payload(previous, "confirm", consent="yes"))
    assert response.status_code == 409
    assert "지난주 밤부터" in state["submission_content"]
    response = client.post("/minwon/chat/turn", json=payload(state, "confirm"))
    assert response.status_code == 422
    state = turn(client, state, "choose_topic", template_id="parking")
    assert state["intake"]["answers"] == {} and "지난주 밤부터" not in state["submission_content"]


@pytest.mark.parametrize("topic_id", ["road", "emergency-welfare"])
def test_positive_safety_choice_skips_questions_and_survives_reprocessing(
    anonymous_client: TestClient, test_app: FastAPI, topic_id: str
) -> None:
    client = anonymous_client
    state = turn(client, review(client), "choose_topic", template_id=topic_id)
    if topic_id == "road":
        state = turn(client, state, "skip_question", field_id="facility_type")
    question = state["current_question"]
    response = client.post(
        "/minwon/chat/turn",
        json=payload(state, "answer_question", field_id=question["field_id"], message="네"),
    )
    assert response.status_code == 422
    test_app.state.chat_provider = UnavailableChatProvider()
    state = turn(
        client,
        state,
        "answer_question",
        field_id=question["field_id"],
        message=question["choices"][0],
    )
    assert state["urgent"] and state["current_question"] is None
    if topic_id == "emergency-welfare":
        assert state["stage"] == "information"
        assert state["intake"]["answers"]["support_topic"]["status"] == "not_asked"
        state = turn(client, state, "complaint")
    state = turn(client, state, "confirm", consent="yes")
    with test_app.state.session_factory() as db:
        complaint = db.scalar(select(Complaint))
        assert complaint and complaint.status == "urgent_review"
        complaint = test_app.state.pipeline.reprocess(db, complaint)
        assert complaint.status == "urgent_review" and complaint.assigned_department_id is None


def test_question_wording_and_negative_choice_do_not_raise_alarm(
    anonymous_client: TestClient,
) -> None:
    client = anonymous_client
    state = turn(client, review(client), "choose_topic", template_id="road")
    state = turn(client, state, "skip_question", field_id="facility_type")
    assert not state["urgent"]
    value = state["current_question"]["choices"][1]
    state = turn(client, state, "answer_question", field_id="hazard_now", message=value)
    assert not state["urgent"] and "위험이 있나요" not in state["submission_content"]


def test_urgent_initial_description_does_not_wait_for_location_or_extra_answers(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    client = anonymous_client
    state = turn(client, start(client), "choose_topic", template_id="road")
    test_app.state.chat_provider = UnavailableChatProvider()
    state = turn(client, state, "say", message="가상 시연 도로에 싱크홀이 생겼어요.")
    assert state["stage"] == "review" and state["urgent"]
    assert all(answer["status"] == "not_asked" for answer in state["intake"]["answers"].values())
    assert state["current_question"] is None


def test_unknown_topic_is_rejected_before_starting_questions(anonymous_client: TestClient) -> None:
    client = anonymous_client
    state = review(client)
    result = client.post(
        "/minwon/chat/turn", json=payload(state, "choose_topic", template_id="not-registered")
    )
    assert result.status_code == 422
    assert client.post("/minwon/chat/open", json={}).json() == state


def test_welfare_context_requires_review_even_if_model_routes_elsewhere(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = anonymous_client
    state = turn(client, review(client), "choose_topic", template_id="activity-support")
    state = turn(client, state, "finish_questions")
    state = turn(client, state, "complaint")
    pipeline = test_app.state.pipeline
    predicted = pipeline.classifier.classify(
        title="가상 가로등", text=DESCRIPTION, location_text="가상 정문"
    )
    assert predicted.category != "welfare"
    monkeypatch.setattr(pipeline.classifier, "classify", lambda **kwargs: predicted)
    turn(client, state, "confirm", consent="yes")
    with test_app.state.session_factory() as db:
        complaint = db.scalar(select(Complaint))
        assert complaint and complaint.status == "needs_review" and complaint.requires_human_review
        complaint = pipeline.reprocess(db, complaint)
        assert complaint.assigned_department_id is None and complaint.requires_human_review
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.complaint_id == complaint.id)))
        assert any(
            "citizen_welfare_intake" in event.details.get("review_reasons", []) for event in events
        )
