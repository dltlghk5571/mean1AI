"""Optional topic selection and one-at-a-time questions; never a topic classifier."""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import TypeAdapter

from app.chat_schemas import AgentReply, ChatDraft, ChatState, ChatTurn
from app.intake_schemas import IntakeAnswer, IntakeQuestion, IntakeState, IntakeTemplate

QUESTION_ACTIONS = {
    "choose_topic",
    "answer_question",
    "skip_question",
    "already_described",
    "finish_questions",
    "revise_question",
}


class QuestionError(ValueError):
    pass


@lru_cache(maxsize=1)
def templates() -> dict[str, IntakeTemplate]:
    path = Path(__file__).resolve().parents[1] / "data" / "citizen_question_templates.json"
    items = TypeAdapter(list[IntakeTemplate]).validate_python(json.loads(path.read_text("utf-8")))
    if len({item.id for item in items}) != len(items) or any(
        len({question.field_id for question in item.questions}) != len(item.questions)
        for item in items
    ):
        raise ValueError("duplicate_intake_identifier")
    return {item.id: item for item in items}


def topic_options() -> list[dict[str, str]]:
    return [
        {"id": item.id, "title": item.title, "purpose": item.purpose}
        for item in templates().values()
    ]


def current_question(state: ChatState) -> IntakeQuestion | None:
    if not state.intake:
        return None
    return next(
        (
            question
            for question in state.intake.template.questions
            if question.field_id == state.intake.editing_field
            or (not state.intake.editing_field and question.field_id not in state.intake.answers)
        ),
        None,
    )


def submission_data(state: ChatState) -> dict[str, str]:
    data = state.draft.model_dump()
    if state.intake:
        lines = [
            f"{question.label}: {answer.value}"
            for question in state.intake.template.questions
            if (answer := state.intake.answers.get(question.field_id))
            and answer.status == "answered"
        ]
        if lines:
            data["content"] += "\n\n[추가로 알려준 내용]\n" + "\n".join(lines)
    if len(data["content"]) > 4000:
        raise QuestionError(
            "본문과 추가 답변을 합쳐 4,000자까지 저장할 수 있어요. "
            "답변을 줄이거나, 이 질문을 건너뛴 뒤 확인 화면에서 본문을 줄여 주세요."
        )
    return data


def finish(state: ChatState, *, urgent: bool = False) -> None:
    assert state.intake
    for question in state.intake.template.questions:
        state.intake.answers.setdefault(
            question.field_id, IntakeAnswer(status="not_asked" if urgent else "skipped")
        )
    state.intake.editing_field = None


def continue_questions(state: ChatState) -> None:
    if (
        state.intake
        and state.intake.purpose == "information"
        and state.intake.template.purpose == "complaint"
        and state.stage in {"location", "review", "questions"}
        and not state.intake.editing_field
    ):
        # Keep complaint questions pending until the citizen chooses intake. Welfare
        # templates still collect their information-specific questions as before.
        state.stage = "information"
        return
    if state.intake and state.urgent and state.stage == "location":
        state.location_checked = True
        state.stage = "review"
    if not state.intake or state.stage not in {"review", "questions"}:
        return
    if state.urgent and not state.intake.editing_field:
        finish(state, urgent=True)
    if current_question(state):
        state.stage = "questions"
    else:
        state.stage = "review" if state.intake.purpose == "complaint" else "information"


def apply_turn(state: ChatState, turn: ChatTurn) -> str | None:
    if turn.action not in QUESTION_ACTIONS:
        return None
    if turn.action == "choose_topic":
        if state.stage not in {
            "welcome",
            "intent",
            "description",
            "location",
            "review",
            "information",
        }:
            raise QuestionError("지금 질문을 마친 뒤 상황을 다시 선택해 주세요.")
        template = templates().get(turn.template_id)
        if not template:
            raise QuestionError("화면에 있는 상황 중에서 선택해 주세요.")
        # Re-selecting a topic must not discard its previous answers.
        if not state.intake or state.intake.template.id != template.id:
            state.intake = IntakeState(
                template=template.model_copy(deep=True), purpose=template.purpose
            )
        if state.stage == "review" or state.draft.location_text:
            state.location_checked = True
        if not state.draft.content and template.purpose == "information":
            state.draft = ChatDraft(title=template.title, content=template.title)
        state.stage = (
            "description"
            if len(state.draft.content) < 5
            else "review"
            if state.location_checked
            else "location"
        )
        return template.title
    if not state.intake:
        raise QuestionError("먼저 화면에서 상황을 선택해 주세요.")
    if turn.action == "revise_question":
        if state.stage not in {"review", "information"} or turn.field_id not in {
            question.field_id for question in state.intake.template.questions
        }:
            raise QuestionError("확인 화면에서 수정할 답변을 골라 주세요.")
        state.intake.editing_field = turn.field_id
        state.stage = "questions"
        return "추가 답변을 수정할게요."
    if state.stage != "questions":
        raise QuestionError("현재 질문 화면에서 답해 주세요.")
    if turn.action == "finish_questions":
        finish(state)
        return "추가 질문은 여기까지 할게요."
    question = current_question(state)
    if not question or turn.field_id != question.field_id:
        raise QuestionError("질문이 바뀌었어요. 현재 질문에 맞게 다시 답해 주세요.")
    if turn.action == "answer_question":
        if not turn.message or len(turn.message) > 500:
            raise QuestionError("답변을 1~500자로 적어 주세요.")
        if question.choices_only and turn.message not in question.choices:
            raise QuestionError("뜻을 정확히 확인할 수 있도록 아래 선택지를 골라 주세요.")
        answer = IntakeAnswer(status="answered", value=turn.message)
        user_text = turn.message
    elif turn.action == "already_described":
        answer = IntakeAnswer(status="in_description")
        user_text = "처음 적은 내용에 이미 설명했어요."
    else:
        answer = IntakeAnswer(status="skipped")
        user_text = "잘 모르겠어요. 이 질문은 건너뛸게요."
    state.intake.answers[question.field_id] = answer
    state.intake.editing_field = None
    return user_text


def local_reply(state: ChatState) -> AgentReply | None:
    if not state.intake:
        return None
    if state.stage == "information":
        if state.urgent:
            return AgentReply(
                next_stage="information",
                message=(
                    "지금 도움이 필요하다고 알려주셔서 나머지 질문은 생략했어요. "
                    "위험한 상황이라면 긴급 신고 창구에 바로 도움을 요청해 주세요. "
                    "이 데모는 신고나 상담을 전달하지 않아요. "
                    "복지 안내는 공식 창구에서도 확인할 수 있어요."
                ),
                source_ids=["bokjiro", "seongnam_handbook"],
            )
        return None
    question = current_question(state) if state.stage == "questions" else None
    messages = {
        "description": "어떤 불편이 있나요? 알고 계신 내용을 편하게 적어 주세요.",
        "location": (
            "어느 구·동의 안내를 원하시나요? 자세한 주소는 필요 없어요. 몰라도 괜찮아요."
            if state.intake.purpose == "information"
            else "어디에서 있었던 일인가요? 주변 건물이나 시설 이름만 알려 주셔도 돼요."
        ),
        "review": "알려주신 내용을 모았어요. 본문과 추가 답변을 확인하고 고친 뒤 접수해 주세요.",
        "questions": question.question if question else "알려주신 내용을 확인할게요.",
    }
    if state.stage not in messages:
        return None
    return AgentReply(next_stage=state.stage, message=messages[state.stage])


def audit_details(state: ChatState) -> dict[str, object]:
    if not state.intake:
        return {}
    return {
        "template_id": state.intake.template.id,
        "template_version": state.intake.template.version,
        "question_statuses": {key: value.status for key, value in state.intake.answers.items()},
        "editing_field": state.intake.editing_field,
    }
