"""Private chat drafts, explicit confirmation, and atomic receipt creation."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chat_schemas import AgentContext, AgentReply, ChatDraft, ChatMessage, ChatState, ChatTurn
from app.extraction_schemas import PendingExtraction
from app.models import CitizenChat, CitizenChatAuditEvent, CitizenSession
from app.services import citizen, extraction_preview
from app.services import citizen_questions as questions
from app.services.audit import record_audit
from app.services.catalog_tools import service_card
from app.services.chat_extraction import ExtractionError, ExtractionRunner
from app.services.chat_provider import ChatAgentProvider
from app.services.citizen_agent import (
    AgentExecution,
    AgentRunError,
    CitizenAgentExecutor,
)
from app.services.citizen_photos import PreparedPhoto, attach_photos
from app.services.emergency import detect_emergency
from app.services.pii import redact_pii
from app.services.pipeline import ComplaintPipeline
from app.services.service_catalog import active_catalog

SOURCES = {
    "bokjiro": {"title": "복지로 · 복지서비스 찾기", "url": "https://www.bokjiro.go.kr/ssis-tbu/"},
    "seongnam_handbook": {
        "title": "성남시 · 민원편람",
        "url": "https://www.seongnam.go.kr/bbs020405",
    },
}
GREETING = (
    "안녕하세요! 우리 동네의 불편이나 궁금한 점을 편하게 이야기해 주세요. "
    "필요한 내용은 하나씩 여쭤볼게요."
)


class ChatError(ValueError):
    def __init__(self, message: str, status: int = 422, *, urgent: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.urgent = urgent


def fresh_state() -> ChatState:
    return ChatState(messages=[ChatMessage(role="assistant", text=GREETING)])


def audit(db: Session, chat: CitizenChat, action: str, **details: object) -> None:
    db.add(
        CitizenChatAuditEvent(
            chat_id=chat.id, revision=chat.revision, action=action, details=details
        )
    )


def find_chat(db: Session, owner_hash: str) -> CitizenChat | None:
    return db.scalar(
        select(CitizenChat)
        .where(CitizenChat.owner_session_hash == owner_hash)
        .execution_options(populate_existing=True)
    )


def open_chat(
    db: Session, session: CitizenSession, extractor: ExtractionRunner | None = None
) -> dict[str, object]:
    chat = find_chat(db, session.token_hash)
    if chat is None:
        chat = CitizenChat(
            owner_session_hash=session.token_hash,
            state=fresh_state().model_dump(mode="json"),
            submission_key=str(uuid4()),
        )
        try:
            db.add(chat)
            db.flush()
            audit(db, chat, "conversation_opened", actor_type="citizen")
            db.commit()
        except IntegrityError:
            db.rollback()
            chat = find_chat(db, session.token_hash)
            if chat is None:
                raise
    return public_state(chat, db, extractor)


def public_state(
    chat: CitizenChat, db: Session, extractor: ExtractionRunner | None = None
) -> dict[str, object]:
    state = ChatState.model_validate(chat.state)
    if state.service_cards:
        catalog = active_catalog(db)
        eligible = (
            {item.id: item for item in catalog.services(datetime.now(UTC).date())}
            if catalog
            else {}
        )
        state.service_cards = [
            service_card(eligible[card.service_id], catalog)
            for card in state.service_cards
            if catalog and card.catalog_version == catalog.version and card.service_id in eligible
        ]
    return {
        "revision": chat.revision,
        **state.model_dump(mode="json", exclude={"source_ids", "extraction"}),
        "extraction_preview": extraction_preview.public_preview(state, extractor),
        "extraction_support": extraction_preview.support(extractor),
        "sources": [SOURCES[source_id] for source_id in state.source_ids],
        "topic_options": questions.topic_options(),
        "current_question": question.model_dump(mode="json")
        if state.stage == "questions" and (question := questions.current_question(state))
        else None,
        "submission_content": questions.submission_data(state)["content"],
        "redirect": f"/minwon/{chat.submitted_complaint_id}/receipt"
        if chat.submitted_complaint_id
        else None,
    }


def clean_turn(turn: ChatTurn) -> ChatTurn:
    expected_fields = {
        "say": {"message"},
        "edit": {"title", "content", "location_text"},
        "confirm": {"consent"},
        "choose_topic": {"template_id"},
        "answer_question": {"field_id", "message"},
        "skip_question": {"field_id"},
        "already_described": {"field_id"},
        "revise_question": {"field_id"},
        "accept_extraction": {"extraction_id"},
        "dismiss_extraction": {"extraction_id"},
    }.get(turn.action, set())
    for field in (
        "message",
        "title",
        "content",
        "location_text",
        "consent",
        "template_id",
        "field_id",
        "extraction_id",
    ):
        if getattr(turn, field) and field not in expected_fields:
            raise ChatError("현재 단계에 맞는 입력인지 확인해 주세요.")
    if turn.action == "answer_question" and len(turn.message) > 500:
        raise ChatError("추가 답변은 500자 이내로 적어 주세요.")
    return turn.model_copy(
        update={
            field: redact_pii(getattr(turn, field).strip()).text
            for field in ("message", "title", "content", "location_text")
        }
    )


def prepare_state(state: ChatState, turn: ChatTurn) -> ChatState:
    if turn.action == "reset":
        return fresh_state()
    state.extraction_notice = None
    user_text = turn.message
    if turn.action == "say" and not user_text:
        raise ChatError("이야기를 한 글자 이상 적어 주세요.")
    question_text = questions.apply_turn(state, turn)
    if question_text is not None:
        user_text = question_text
    elif state.stage in {"welcome", "intent", "information"}:
        if turn.action == "information":
            state.stage = "information"
            user_text = "복지·생활정보를 알아볼게요."
        elif turn.action == "complaint":
            state.stage = "location" if len(state.draft.content) >= 5 else "description"
            if state.intake:
                state.intake.purpose = "complaint"
                if state.stage == "location" and state.location_checked:
                    state.stage = "review"
            user_text = "민원으로 접수할게요."
        elif turn.action == "say":
            state.draft = ChatDraft(title=user_text.splitlines()[0][:80], content=user_text)
            state.intake = None
            state.location_checked = False
            state.stage = "intent"
        else:
            raise ChatError("아래 선택지에서 이어갈 내용을 골라 주세요.")
    elif state.stage == "description" and turn.action == "say":
        if len(user_text) < 5:
            raise ChatError("불편한 상황을 다섯 글자 이상으로 알려 주세요.")
        state.draft = ChatDraft(title=user_text.splitlines()[0][:80], content=user_text)
        state.stage = "location"
    elif state.stage == "location" and turn.action in {"say", "skip_location"}:
        if len(user_text) > 300:
            raise ChatError("장소는 300자 이내로 알려 주세요.")
        state.draft.location_text = user_text
        state.location_checked = True
        user_text = user_text or "정확한 장소를 모르겠어요."
        state.stage = "review"
    elif state.stage == "review" and turn.action == "edit":
        payload = citizen.validate_submission(
            {"title": turn.title, "content": turn.content, "location_text": turn.location_text},
            submitting=False,
        )
        state.draft = ChatDraft(**citizen.preview_submission(payload))
        if state.intake:
            state.intake.answers = {
                key: answer
                for key, answer in state.intake.answers.items()
                if not (
                    answer.status == "in_description"
                    and answer.value
                    and answer.value not in state.draft.content
                )
            }
        user_text = "접수할 내용을 수정했어요."
    else:
        raise ChatError("현재 화면의 안내에 따라 진행해 주세요.")
    if len(state.messages) >= 38:
        raise ChatError("대화가 길어졌어요. 새 대화를 시작하거나 직접 작성 화면을 이용해 주세요.")
    state.messages.append(ChatMessage(role="user", text=user_text))
    state.urgent = state.urgent or bool(
        detect_emergency(f"{user_text}\n{state.draft.model_dump_json()}").signals
    )
    questions.continue_questions(state)
    questions.submission_data(state)
    return state


def advance_chat(
    db: Session,
    session: CitizenSession,
    owner_token: str,
    turn: ChatTurn,
    provider: ChatAgentProvider,
    pipeline: ComplaintPipeline,
    executor: CitizenAgentExecutor | None = None,
    photos: tuple[PreparedPhoto, ...] = (),
    extractor: ExtractionRunner | None = None,
) -> dict[str, object]:
    turn = clean_turn(turn)
    if photos and turn.action != "confirm":
        raise ChatError("사진은 최종 접수 확인과 함께 저장할 수 있어요.")
    fingerprint_data = turn.model_dump()
    # Keep pre-extraction request retries compatible after adding an optional envelope field.
    if not turn.extraction_id:
        fingerprint_data.pop("extraction_id")
    if photos:
        fingerprint_data["photo_hashes"] = [photo.source_hash for photo in photos]
    fingerprint = citizen.digest(json.dumps(fingerprint_data, sort_keys=True, ensure_ascii=False))
    chat = find_chat(db, session.token_hash)
    if chat is None:
        raise ChatError("대화를 먼저 열어 주세요. 페이지를 새로고침하면 이어갈 수 있어요.", 409)
    if chat.last_request_id == turn.request_id:
        if chat.last_request_hash != fingerprint:
            raise ChatError("이미 처리한 요청과 내용이 달라요. 최신 대화를 불러와 주세요.", 409)
        return public_state(chat, db, extractor)
    if chat.revision != int(turn.revision):
        raise ChatError("다른 탭에서 대화가 바뀌었어요. 최신 대화를 불러와 주세요.", 409)
    state = ChatState.model_validate(chat.state)
    execution: AgentExecution | None = None
    extraction_events: list[dict[str, object]] = []
    accepted_extraction: ChatState | None = None
    previous_stage = state.stage
    if state.extraction and turn.action not in {"accept_extraction", "dismiss_extraction", "reset"}:
        raise ChatError("정리한 내용을 확인하거나 직접 선택해서 계속해 주세요.", 409)
    if turn.action == "confirm":
        if turn.consent != "yes":
            raise ChatError("데모 접수 안내를 확인하고 동의해 주세요.")
        if chat.submitted_complaint_id:
            return public_state(chat, db, extractor)
        if state.stage != "review":
            raise ChatError("접수 내용을 먼저 확인해 주세요.")
        citizen.validate_submission(questions.submission_data(state), submitting=False)
        state.stage = "submitted"
        state.messages.append(ChatMessage(role="assistant", text="데모 민원 접수가 완료됐어요."))
    else:
        try:
            if turn.action in {"accept_extraction", "dismiss_extraction"}:
                pending = state.extraction
                if pending is None or str(pending.request_id) != turn.extraction_id:
                    raise ChatError("확인할 내용이 바뀌었어요. 최신 대화를 불러와 주세요.", 409)
                if len(state.messages) >= 38:
                    raise ChatError("대화가 길어졌어요. 새 대화를 시작하거나 직접 작성해 주세요.")
                if turn.action == "accept_extraction":
                    if not extraction_preview.current(state, extractor):
                        raise ChatError("정리 기준이 바뀌었어요. 직접 선택해서 계속해 주세요.", 409)
                    accepted_extraction = state.model_copy(deep=True)
                    extraction_preview.accept(state, extractor)
                else:
                    state.extraction = None
                    state.extraction_notice = None
                state.messages.append(
                    ChatMessage(
                        role="user",
                        text="정리한 내용이 맞아요. 이 내용으로 계속할게요."
                        if turn.action == "accept_extraction"
                        else "직접 선택해서 계속할게요.",
                    )
                )
                extraction_events.append(
                    {
                        "status": "accepted" if turn.action == "accept_extraction" else "dismissed",
                        "request_id": str(pending.request_id),
                        "provider": pending.provider,
                        "model_id": pending.model_id,
                        "actor_type": "citizen",
                    }
                )
            else:
                state = prepare_state(state, turn)
        except questions.QuestionError as exc:
            raise ChatError(str(exc)) from None
        extraction_attempted = False
        if (
            extractor
            and extractor.enabled
            and turn.action == "say"
            and previous_stage in {"welcome", "intent", "information"}
            and len(turn.message) >= 5
        ):
            extraction_attempted = True
            if state.urgent:
                state.extraction_notice = "urgent"
                extraction_events.append({"status": "skipped", "reason": "urgent"})
            else:
                request = extractor.make_request(state.draft.content)
                metadata = {
                    "request_id": str(request.request_id),
                    "input_hash": request.input_hash,
                    "model_id": request.model_id,
                    "provider": extractor.settings.chat_extraction_provider,
                }
                audit(db, chat, "extraction_requested", **metadata)
                # Authorize/audit first, then release the transaction before bounded model I/O.
                db.commit()
                try:
                    extracted = extractor.run(request)
                    if extracted.proposal.abstained:
                        state.extraction_notice = "abstained"
                    else:
                        state.extraction = PendingExtraction(
                            **extracted.model_dump(), provider=metadata["provider"]
                        )
                        if not extraction_preview.current(state, extractor):
                            raise ExtractionError("extraction_context_changed")
                    extraction_events.append(
                        {
                            **metadata,
                            "status": "abstained" if extracted.proposal.abstained else "proposed",
                        }
                    )
                except ExtractionError:
                    state.extraction = None
                    state.extraction_notice = "failed"
                    extraction_events.append({**metadata, "status": "failed"})
        if turn.action != "reset":
            try:
                local_reply = questions.local_reply(state)
                if state.extraction or extraction_attempted or turn.action == "dismiss_extraction":
                    local_reply = AgentReply(
                        next_stage=state.stage,
                        message="이렇게 이해했어요. 맞는지 확인해 주세요. 아직 접수되지는 않았어요."
                        if state.extraction
                        else "어떤 도움을 원하시나요? 아래에서 직접 선택해 주세요.",
                    )
                provider_state = state.model_copy(deep=True)
                # Approved-source lookup can use answers, but cannot mutate the stored draft.
                provider_state.draft = ChatDraft(**questions.submission_data(state))
                context = AgentContext(
                    state=provider_state,
                    action=turn.action,
                    expected_stage=state.stage,
                )
                # Revalidate adapters; output cannot choose submission, draft contents, or URLs.
                if local_reply:
                    reply = local_reply
                    state.service_cards = []
                elif executor:
                    execution = executor.execute(db, context)
                    reply = AgentReply.model_validate(execution.reply.model_dump())
                    state.service_cards = execution.cards
                else:
                    reply = AgentReply.model_validate(provider.respond(context).model_dump())
                    state.service_cards = []
                if reply.next_stage != state.stage or (
                    state.stage != "information" and reply.source_ids
                ):
                    raise ValueError("invalid_chat_transition")
                state.source_ids = reply.source_ids
                state.messages.append(
                    ChatMessage(role="assistant", text=redact_pii(reply.message).text)
                )
            except Exception as exc:
                db.rollback()
                if isinstance(exc, AgentRunError):
                    for event in exc.events:
                        audit(db, chat, "agent_step_attempted", **event)
                for event in extraction_events:
                    audit(db, chat, "extraction_aborted", **event)
                audit(
                    db,
                    chat,
                    "provider_failed",
                    provider=provider.provider_name,
                    urgent=state.urgent,
                )
                db.commit()
                raise ChatError(
                    "대화를 이어가지 못했어요. 입력한 내용은 남아 있으니 다시 시도해 주세요.",
                    503,
                    urgent=state.urgent,
                ) from None

    # CAS makes stale tabs and concurrent confirmations safe without holding a lock over inference.
    changed = db.execute(
        update(CitizenChat)
        .where(CitizenChat.id == chat.id, CitizenChat.revision == int(turn.revision))
        .values(revision=CitizenChat.revision + 1)
        .returning(CitizenChat.id)
        .execution_options(synchronize_session=False)
    )
    if changed.scalar_one_or_none() is None:
        db.rollback()
        if execution:
            for event in execution.events:
                audit(db, chat, "agent_step_aborted", **event)
        for event in extraction_events:
            audit(db, chat, "extraction_aborted", **event)
        if execution or extraction_events:
            db.commit()
        latest = find_chat(db, session.token_hash)
        if (
            latest
            and latest.last_request_id == turn.request_id
            and latest.last_request_hash == fingerprint
        ):
            return public_state(latest, db, extractor)
        raise ChatError("다른 요청이 먼저 반영됐어요. 최신 대화를 불러와 주세요.", 409)

    try:
        if (state.extraction and not extraction_preview.current(state, extractor)) or (
            accepted_extraction and not extraction_preview.current(accepted_extraction, extractor)
        ):
            raise ChatError("정리 기준이 바뀌었어요. 최신 대화를 불러와 주세요.", 409)
        if execution:
            # The chat CAS holds the SQLite writer lock while the catalog pin is checked.
            execution.verify_catalog(db)
        chat.revision = int(turn.revision) + 1
        chat.state = state.model_dump(mode="json")
        chat.last_request_id = turn.request_id
        chat.last_request_hash = fingerprint
        if turn.action == "reset":
            chat.submission_key = str(uuid4())
            chat.submitted_complaint_id = None
        if turn.action == "confirm":
            submission = citizen.stage_submission(
                db,
                pipeline,
                session,
                owner_token,
                {
                    **questions.submission_data(state),
                    "consent": "yes",
                    "request_key": chat.submission_key,
                },
                welfare_intake=bool(
                    state.intake and state.intake.template.purpose == "information"
                ),
            )
            chat.submitted_complaint_id = submission.complaint_id
            photo_ids = attach_photos(db, submission.complaint_id, photos) if photos else []
            record_audit(
                db,
                complaint_id=submission.complaint_id,
                action="citizen_chat_confirmed",
                actor_type="citizen",
                details={
                    "chat_id": chat.id,
                    "revision": chat.revision,
                    "demo_consent": True,
                    "photo_ids": photo_ids,
                    **questions.audit_details(state),
                },
            )
        audit(
            db,
            chat,
            "citizen_confirmed" if turn.action == "confirm" else "conversation_advanced",
            actor_type="citizen"
            if turn.action in {"reset", "confirm", "accept_extraction", "dismiss_extraction"}
            else "rules",
            provider="intake_questions" if questions.local_reply(state) else provider.provider_name,
            input_action=turn.action,
            stage=state.stage,
            urgent=state.urgent,
            source_ids=state.source_ids,
            complaint_id=chat.submitted_complaint_id,
            **questions.audit_details(state),
        )
        if execution:
            for event in execution.events:
                audit(db, chat, "agent_step_completed", **event)
        for event in extraction_events:
            audit(db, chat, "extraction_resolved", **event)
        db.commit()
    except Exception:
        db.rollback()
        if execution:
            for event in execution.events:
                audit(db, chat, "agent_step_aborted", **event)
        for event in extraction_events:
            audit(db, chat, "extraction_aborted", **event)
        if execution or extraction_events:
            db.commit()
        raise
    return public_state(chat, db, extractor)
