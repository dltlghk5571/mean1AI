"""Turn a validated, current proposal into a citizen-confirmed draft. Never submit here."""

from app.chat_schemas import ChatState
from app.extraction_schemas import validate_proposal
from app.intake_schemas import IntakeAnswer, IntakeState
from app.services import citizen_questions as questions
from app.services.chat_extraction import DEMO_TEXT, ExtractionRunner


def current(state: ChatState, runner: ExtractionRunner | None) -> bool:
    pending = state.extraction
    if pending is None or runner is None or not runner.enabled or state.stage != "intent":
        return False
    try:
        request = runner.make_request(state.draft.content)
        if (
            pending.provider != runner.settings.chat_extraction_provider
            or pending.model_id != request.model_id
            or pending.input_hash != request.input_hash
            or pending.proposal.abstained
            or pending.execution_mode != ("synthetic" if pending.provider == "demo" else "model")
        ):
            return False
        validate_proposal(request, pending.proposal)
        return True
    except ValueError:
        return False


def accept(state: ChatState, runner: ExtractionRunner | None) -> None:
    if not current(state, runner):
        raise ValueError("extraction_preview_stale")
    assert state.extraction and state.extraction.proposal.purpose
    proposal = state.extraction.proposal
    purpose = state.extraction.proposal.purpose.value
    if proposal.topic:
        state.intake = IntakeState(
            template=questions.templates()[proposal.topic.template_id].model_copy(deep=True),
            purpose=purpose,
            answers={
                item.field_id: IntakeAnswer(status="in_description", value=item.value)
                for item in proposal.answers
            },
        )
    state.draft.location_text = proposal.location.value if proposal.location else ""
    state.location_checked = proposal.location is not None
    # The citizen's original text stays intact; quoted answers already exist in that text.
    state.stage = (
        "information"
        if purpose == "information" and not state.intake
        else "review"
        if state.location_checked
        else "location"
    )
    state.extraction = None
    state.extraction_notice = None
    questions.continue_questions(state)
    questions.submission_data(state)


def public_preview(state: ChatState, runner: ExtractionRunner | None) -> dict[str, object] | None:
    pending = state.extraction
    if pending is None:
        return None
    valid = current(state, runner)
    rows: list[dict[str, str]] = []
    if valid:
        proposal = pending.proposal
        assert proposal.purpose
        rows.append(
            {
                "label": "원하는 도움",
                "value": "민원 접수 준비"
                if proposal.purpose.value == "complaint"
                else "정보 알아보기",
                "quote": proposal.purpose.quote,
            }
        )
        if proposal.topic:
            template = questions.templates()[proposal.topic.template_id]
            rows.append(
                {"label": "이야기한 상황", "value": template.title, "quote": proposal.topic.quote}
            )
            labels = {item.field_id: item.label for item in template.questions}
            rows.extend(
                {"label": labels[item.field_id], "value": item.value, "quote": item.quote}
                for item in proposal.answers
            )
        if proposal.location:
            rows.insert(
                2,
                {
                    "label": "장소",
                    "value": proposal.location.value,
                    "quote": proposal.location.quote,
                },
            )
    return {
        "id": str(pending.request_id),
        "stale": not valid,
        "synthetic": pending.provider == "demo",
        "rows": rows,
    }


def support(runner: ExtractionRunner | None) -> dict[str, object]:
    mode = runner.settings.chat_extraction_provider if runner else "off"
    return {"mode": mode, "example": DEMO_TEXT if mode == "demo" else None}
