"""Private follow-up history and a human response queue, independent of model availability."""

import hmac
import time
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    CitizenFollowUp,
    CitizenFollowUpReply,
    CitizenSession,
    CitizenSubmission,
    Complaint,
)
from app.services.ai_queue import lock_complaint
from app.services.audit import record_audit
from app.services.auth import AuthenticatedUser, require_role
from app.services.citizen import korean_time
from app.services.emergency import detect_emergency
from app.services.pii import redact_pii
from app.services.policy import evaluate_policy

PAGE_SIZE = 20


class FollowUpInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_key: UUID
    body: str = Field(min_length=2, max_length=2000)
    confirmed: Literal["yes"]


class ReplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    body: str = Field(min_length=5, max_length=4000)
    confirmed: Literal["yes"]


class FollowUpError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def add_followup(
    db: Session, session: CitizenSession, complaint_id: str, data: dict[str, str]
) -> CitizenFollowUp:
    submission = db.get(CitizenSubmission, complaint_id)
    # Lookup-code grants intentionally remain read-only, including on another device.
    if (
        submission is None
        or submission.owner_session_hash != session.token_hash
        or session.expires_at <= int(time.time())
    ):
        raise FollowUpError("접수한 브라우저의 유효한 세션에서만 추가 문의를 남길 수 있어요.", 404)
    try:
        payload = FollowUpInput.model_validate(data)
    except ValidationError:
        raise FollowUpError("내용을 2~2,000자로 적고, 전달 동의를 확인해 주세요.") from None
    fingerprint = hmac.new(session.csrf_token.encode(), payload.body.encode(), "sha256").hexdigest()
    complaint = db.get(Complaint, complaint_id)
    assert complaint is not None
    lock_complaint(db, complaint)
    existing = db.scalar(
        select(CitizenFollowUp).where(
            CitizenFollowUp.complaint_id == complaint_id,
            CitizenFollowUp.request_key == str(payload.request_key),
        )
    )
    if existing:
        if not hmac.compare_digest(existing.request_hash, fingerprint):
            raise FollowUpError(
                "앞선 문의가 이미 저장되었어요. 새로고침해 확인한 뒤 새 문의를 남겨 주세요.", 409
            )
        db.commit()
        return existing
    redacted = redact_pii(payload.body)
    emergency = detect_emergency(redacted.text)
    policy = evaluate_policy(redacted.text, complaint.category or "other")
    reasons = [*emergency.signals, *policy.reasons]
    urgency = emergency.urgency.value
    if any(
        reason in reasons
        for reason in ("sensitive_signal:self_harm", "sensitive_signal:abuse_or_violence")
    ):
        urgency = "critical"
    followup = CitizenFollowUp(
        complaint_id=complaint_id,
        request_key=str(payload.request_key),
        request_hash=fingerprint,
        body=redacted.text,
        urgency=urgency,
        review_reasons=reasons,
    )
    db.add(followup)
    db.flush()
    record_audit(
        db,
        complaint_id=complaint_id,
        action="citizen_followup_added",
        actor_type="citizen",
        details={
            "followup_id": followup.id,
            "safety_provider": "rules",
            "urgency": urgency,
            "review_reasons": reasons,
            "pii_types": redacted.detected_types,
            "requires_human_review": True,
            "external_message_sent": False,
        },
    )
    db.commit()
    return followup


def publish_followup_reply(
    db: Session,
    user: AuthenticatedUser,
    complaint_id: str,
    followup_id: str,
    data: dict[str, str],
) -> CitizenFollowUpReply:
    require_role(user, "reviewer")
    followup = db.get(CitizenFollowUp, followup_id)
    if followup is None or followup.complaint_id != complaint_id:
        raise FollowUpError("추가 문의를 찾을 수 없습니다.", 404)
    try:
        payload = ReplyInput.model_validate(data)
    except ValidationError:
        raise FollowUpError("답변을 5~4,000자로 적고 시민 공개 내용을 확인해 주세요.") from None
    if redact_pii(payload.body).detected_types:
        raise FollowUpError("답변에서 전화번호·이메일·주민등록번호 형식을 지워 주세요.")
    complaint = db.get(Complaint, complaint_id)
    assert complaint is not None
    lock_complaint(db, complaint)
    existing = db.get(CitizenFollowUpReply, followup_id)
    if existing:
        if existing.body != payload.body:
            raise FollowUpError("이미 공개된 답변이 있습니다. 새로고침해 확인해 주세요.", 409)
        db.commit()
        return existing
    reply = CitizenFollowUpReply(followup_id=followup_id, actor_id=user.username, body=payload.body)
    db.add(reply)
    record_audit(
        db,
        complaint_id=complaint_id,
        action="citizen_followup_reply_published",
        actor_type="officer",
        actor_id=user.username,
        details={"followup_id": followup_id, "external_message_sent": False},
    )
    db.commit()
    return reply


def _public_item(followup: CitizenFollowUp, reply: CitizenFollowUpReply | None) -> dict[str, Any]:
    # No owner identifiers, request hashes, staff identities, or internal drafts in citizen views.
    return {
        "id": followup.id,
        "body": redact_pii(followup.body).text,
        "created_at": korean_time(followup.created_at),
        "urgent": followup.urgency != "normal",
        "sensitive": bool(followup.review_reasons),
        "answer": redact_pii(reply.body).text if reply else None,
        "answered_at": korean_time(reply.published_at) if reply else None,
    }


def history(db: Session, complaint_id: str, page: int = 1) -> dict[str, Any]:
    total = (
        db.scalar(
            select(func.count())
            .select_from(CitizenFollowUp)
            .where(CitizenFollowUp.complaint_id == complaint_id)
        )
        or 0
    )
    page = min(max(1, page), max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
    rows = db.execute(
        select(CitizenFollowUp, CitizenFollowUpReply)
        .outerjoin(CitizenFollowUpReply)
        .where(CitizenFollowUp.complaint_id == complaint_id)
        .order_by(CitizenFollowUp.created_at.desc(), CitizenFollowUp.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    return {
        "items": [_public_item(item, reply) for item, reply in rows],
        "total": total,
        "page": page,
        "has_next": page * PAGE_SIZE < total,
    }


def pending_queue(db: Session, page: int = 1) -> dict[str, Any]:
    pending = (
        ~select(CitizenFollowUpReply)
        .where(CitizenFollowUpReply.followup_id == CitizenFollowUp.id)
        .exists()
    )
    total = db.scalar(select(func.count()).select_from(CitizenFollowUp).where(pending)) or 0
    page = min(max(1, page), max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
    rows = db.execute(
        select(CitizenFollowUp, CitizenSubmission.receipt_number, Complaint.redacted_title)
        .join(CitizenSubmission, CitizenSubmission.complaint_id == CitizenFollowUp.complaint_id)
        .join(Complaint, Complaint.id == CitizenFollowUp.complaint_id)
        .where(pending)
        .order_by(
            case(
                (CitizenFollowUp.urgency == "critical", 0),
                (CitizenFollowUp.urgency == "high", 1),
                else_=2,
            ),
            CitizenFollowUp.created_at,
            CitizenFollowUp.id,
        )
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    return {
        "items": [
            {
                **_public_item(item, None),
                "complaint_id": item.complaint_id,
                "receipt_number": number,
                "title": redact_pii(title).text,
            }
            for item, number, title in rows
        ],
        "total": total,
        "page": page,
        "has_next": page * PAGE_SIZE < total,
    }
