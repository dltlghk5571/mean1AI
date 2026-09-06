"""Private chat drafts, explicit confirmation, and atomic receipt creation."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chat_schemas import AgentContext, AgentReply, ChatDraft, ChatMessage, ChatState, ChatTurn
from app.models import CitizenChat, CitizenChatAuditEvent, CitizenSession
from app.services import citizen
from app.services.audit import record_audit
from app.services.chat_provider import ChatAgentProvider
from app.services.citizen_agent import (
    AgentExecution,
    AgentRunError,
    CitizenAgentExecutor,
    service_card,
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


def open_chat(db: Session, session: CitizenSession) -> dict[str, object]:
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
    return public_state(chat, db)


def public_state(chat: CitizenChat, db: Session) -> dict[str, object]:
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
        **state.model_dump(mode="json", exclude={"source_ids"}),
        "sources": [SOURCES[source_id] for source_id in state.source_ids],
        "redirect": f"/minwon/{chat.submitted_complaint_id}/receipt"
        if chat.submitted_complaint_id
        else None,
    }


def clean_turn(turn: ChatTurn) -> ChatTurn:
    expected_fields = {
        "say": {"message"},
        "edit": {"title", "content", "location_text"},
        "confirm": {"consent"},
    }.get(turn.action, set())
    for field in ("message", "title", "content", "location_text", "consent"):
        if getattr(turn, field) and field not in expected_fields:
            raise ChatError("현재 단계에 맞는 입력인지 확인해 주세요.")
    return turn.model_copy(
        update={
            field: redact_pii(getattr(turn, field).strip()).text
            for field in ("message", "title", "content", "location_text")
        }
    )


def prepare_state(state: ChatState, turn: ChatTurn) -> ChatState:
    if turn.action == "reset":
        return fresh_state()
    user_text = turn.message
    if turn.action == "say" and not user_text:
        raise ChatError("이야기를 한 글자 이상 적어 주세요.")
    if state.stage in {"welcome", "intent", "information"}:
        if turn.action == "information":
            state.stage = "information"
            user_text = "복지·생활정보를 알아볼게요."
        elif turn.action == "complaint":
            state.stage = "location" if len(state.draft.content) >= 5 else "description"
            user_text = "민원으로 접수할게요."
        elif turn.action == "say":
            state.draft = ChatDraft(title=user_text.splitlines()[0][:80], content=user_text)
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
        user_text = user_text or "정확한 장소를 모르겠어요."
        state.stage = "review"
    elif state.stage == "review" and turn.action == "edit":
        payload = citizen.validate_submission(
            {"title": turn.title, "content": turn.content, "location_text": turn.location_text},
            submitting=False,
        )
        state.draft = ChatDraft(**citizen.preview_submission(payload))
        user_text = "접수할 내용을 수정했어요."
    else:
        raise ChatError("현재 화면의 안내에 따라 진행해 주세요.")
    if len(state.messages) >= 38:
        raise ChatError("대화가 길어졌어요. 새 대화를 시작하거나 직접 작성 화면을 이용해 주세요.")
    state.messages.append(ChatMessage(role="user", text=user_text))
    state.urgent = state.urgent or bool(
        detect_emergency(f"{user_text}\n{state.draft.model_dump_json()}").signals
    )
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
) -> dict[str, object]:
    turn = clean_turn(turn)
    if photos and turn.action != "confirm":
        raise ChatError("사진은 최종 접수 확인과 함께 저장할 수 있어요.")
    fingerprint_data = turn.model_dump()
    if photos:
        fingerprint_data["photo_hashes"] = [photo.source_hash for photo in photos]
    fingerprint = citizen.digest(json.dumps(fingerprint_data, sort_keys=True, ensure_ascii=False))
    chat = find_chat(db, session.token_hash)
    if chat is None:
        raise ChatError("대화를 먼저 열어 주세요. 페이지를 새로고침하면 이어갈 수 있어요.", 409)
    if chat.last_request_id == turn.request_id:
        if chat.last_request_hash != fingerprint:
            raise ChatError("이미 처리한 요청과 내용이 달라요. 최신 대화를 불러와 주세요.", 409)
        return public_state(chat, db)
    if chat.revision != int(turn.revision):
        raise ChatError("다른 탭에서 대화가 바뀌었어요. 최신 대화를 불러와 주세요.", 409)
    state = ChatState.model_validate(chat.state)
    execution: AgentExecution | None = None
    if turn.action == "confirm":
        if turn.consent != "yes":
            raise ChatError("데모 접수 안내를 확인하고 동의해 주세요.")
        if chat.submitted_complaint_id:
            return public_state(chat, db)
        if state.stage != "review":
            raise ChatError("접수 내용을 먼저 확인해 주세요.")
        citizen.validate_submission(state.draft.model_dump(), submitting=False)
        state.stage = "submitted"
        state.messages.append(ChatMessage(role="assistant", text="데모 민원 접수가 완료됐어요."))
    else:
        state = prepare_state(state, turn)
        if turn.action != "reset":
            try:
                context = AgentContext(
                    state=state.model_copy(deep=True),
                    action=turn.action,
                    expected_stage=state.stage,
                )
                # Revalidate adapters; output cannot choose submission, draft contents, or URLs.
                if executor:
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
            db.commit()
        latest = find_chat(db, session.token_hash)
        if (
            latest
            and latest.last_request_id == turn.request_id
            and latest.last_request_hash == fingerprint
        ):
            return public_state(latest, db)
        raise ChatError("다른 요청이 먼저 반영됐어요. 최신 대화를 불러와 주세요.", 409)

    try:
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
                {**state.draft.model_dump(), "consent": "yes", "request_key": chat.submission_key},
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
                },
            )
        audit(
            db,
            chat,
            "citizen_confirmed" if turn.action == "confirm" else "conversation_advanced",
            actor_type="citizen" if turn.action in {"reset", "confirm"} else "rules",
            provider=provider.provider_name,
            input_action=turn.action,
            stage=state.stage,
            urgent=state.urgent,
            source_ids=state.source_ids,
            complaint_id=chat.submitted_complaint_id,
        )
        if execution:
            for event in execution.events:
                audit(db, chat, "agent_step_completed", **event)
        db.commit()
    except Exception:
        db.rollback()
        if execution:
            for event in execution.events:
                audit(db, chat, "agent_step_aborted", **event)
            db.commit()
        raise
    return public_state(chat, db)
