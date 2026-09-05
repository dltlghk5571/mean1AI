"""Private citizen access, atomic intake, and explicit reply publication."""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    CitizenGrant,
    CitizenSession,
    CitizenSubmission,
    Complaint,
    PublishedReply,
    ReviewDecision,
)
from app.schemas import ComplaintCreate
from app.services.ai_queue import lock_complaint
from app.services.audit import record_audit
from app.services.auth import AuthenticatedUser, require_role
from app.services.pii import redact_pii
from app.services.pipeline import ComplaintPipeline

COOKIE_NAME = "minwon_citizen_session"
SESSION_TTL = 30 * 24 * 60 * 60


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_session(db: Session, token: str | None) -> CitizenSession | None:
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    session = db.get(CitizenSession, digest(token))
    if session is None or session.expires_at <= int(time.time()):
        return None
    return session


def create_session(db: Session) -> tuple[CitizenSession, str]:
    token = secrets.token_urlsafe(32)
    session = CitizenSession(
        token_hash=digest(token),
        csrf_token=secrets.token_urlsafe(24),
        expires_at=int(time.time()) + SESSION_TTL,
    )
    db.add(session)
    db.commit()
    return session, token


def valid_csrf(session: CitizenSession | None, token: str | None) -> bool:
    return bool(
        session and token and hmac.compare_digest(session.csrf_token.encode(), token.encode())
    )


def lookup_code(owner_token: str, request_key: str) -> str:
    """Owner can recover the code; the DB stores only its hash, never this value."""
    raw = hmac.new(
        owner_token.encode(), f"citizen-lookup:{request_key}".encode(), "sha256"
    ).digest()
    code = base64.b32encode(raw).decode()[:20]
    return "-".join(code[i : i + 5] for i in range(0, 20, 5))


def code_digest(code: str) -> str:
    return digest(re.sub(r"[\s-]", "", code).upper())


class CitizenValidationError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("citizen_input_invalid")
        self.errors = errors


def validate_submission(data: dict[str, str], *, submitting: bool) -> ComplaintCreate:
    errors: dict[str, str] = {}
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    location = data.get("location_text", "").strip()
    if not 2 <= len(title) <= 200:
        errors["title"] = "제목을 2~200자로 적어 주세요."
    if not 5 <= len(content) <= 20_000:
        errors["content"] = "불편한 내용을 5~20,000자로 적어 주세요."
    if len(location) > 300:
        errors["location_text"] = "위치는 300자 이내로 적어 주세요."
    if submitting:
        if data.get("consent") != "yes":
            errors["consent"] = "데모 접수 안내를 확인하고 동의해 주세요."
        try:
            UUID(data.get("request_key", ""))
        except ValueError:
            errors["request_key"] = "접수 정보를 확인할 수 없습니다. 페이지를 새로 열어 주세요."
    if errors:
        raise CitizenValidationError(errors)
    return ComplaintCreate(title=title, content=content, location_text=location or None)


def preview_submission(payload: ComplaintCreate) -> dict[str, str]:
    return {
        "title": redact_pii(payload.title).text,
        "content": redact_pii(payload.content).text,
        "location_text": redact_pii(payload.location_text or "").text,
    }


def previous_submission(db: Session, owner_hash: str, request_key: str) -> CitizenSubmission | None:
    return db.scalar(
        select(CitizenSubmission).where(
            CitizenSubmission.owner_session_hash == owner_hash,
            CitizenSubmission.request_key == request_key,
        )
    )


def submit(
    db: Session,
    pipeline: ComplaintPipeline,
    session: CitizenSession,
    owner_token: str,
    data: dict[str, str],
) -> CitizenSubmission:
    payload = validate_submission(data, submitting=True)
    request_key = str(UUID(data["request_key"]))
    existing = previous_submission(db, session.token_hash, request_key)
    if existing:
        return existing
    try:
        complaint = pipeline.create_and_process(db, payload, commit=False)
        korean_date = (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y%m%d")
        submission = CitizenSubmission(
            complaint_id=complaint.id,
            receipt_number=f"SN-{korean_date}-{secrets.token_hex(5).upper()}",
            owner_session_hash=session.token_hash,
            request_key=request_key,
            lookup_code_hash=code_digest(lookup_code(owner_token, request_key)),
        )
        db.add(submission)
        record_audit(
            db,
            complaint_id=complaint.id,
            action="citizen_access_created",
            actor_type="citizen",
            details={"access": "private", "demo_consent": True},
        )
        db.commit()
        return submission
    except IntegrityError:
        db.rollback()
        existing = previous_submission(db, session.token_hash, request_key)
        if existing:
            return existing
        raise
    except Exception:
        db.rollback()
        raise


def accessible_submissions(db: Session, session: CitizenSession) -> list[CitizenSubmission]:
    grants = select(CitizenGrant.complaint_id).where(
        CitizenGrant.session_hash == session.token_hash
    )
    return list(
        db.scalars(
            select(CitizenSubmission)
            .join(Complaint)
            .where(
                or_(
                    CitizenSubmission.owner_session_hash == session.token_hash,
                    CitizenSubmission.complaint_id.in_(grants),
                )
            )
            .order_by(Complaint.created_at.desc())
        ).all()
    )


def get_accessible(
    db: Session, session: CitizenSession | None, complaint_id: str
) -> CitizenSubmission | None:
    if session is None:
        return None
    submission = db.get(CitizenSubmission, complaint_id)
    if submission and (
        submission.owner_session_hash == session.token_hash
        or db.get(CitizenGrant, (session.token_hash, complaint_id)) is not None
    ):
        return submission
    return None


def grant_lookup(
    db: Session, session: CitizenSession, receipt_number: str, code: str
) -> CitizenSubmission | None:
    submission = db.scalar(
        select(CitizenSubmission).where(
            CitizenSubmission.receipt_number == receipt_number.strip().upper()
        )
    )
    expected = submission.lookup_code_hash if submission else "0" * 64
    if not hmac.compare_digest(code_digest(code), expected) or submission is None:
        return None
    if (
        submission.owner_session_hash != session.token_hash
        and db.get(CitizenGrant, (session.token_hash, submission.complaint_id)) is None
    ):
        db.add(CitizenGrant(session_hash=session.token_hash, complaint_id=submission.complaint_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if db.get(CitizenGrant, (session.token_hash, submission.complaint_id)) is None:
                raise
    return submission


def latest_reply(db: Session, complaint_id: str) -> PublishedReply | None:
    return db.scalar(
        select(PublishedReply)
        .where(PublishedReply.complaint_id == complaint_id)
        .order_by(PublishedReply.id.desc())
        .limit(1)
    )


def public_record(db: Session, submission: CitizenSubmission) -> dict[str, object]:
    """An explicit allowlist; raw ORM complaint objects never enter citizen templates."""
    complaint = db.get(Complaint, submission.complaint_id)
    assert complaint is not None
    reply = latest_reply(db, complaint.id)
    state = "answered" if reply else ("received" if complaint.status == "received" else "checking")
    return {
        "id": complaint.id,
        "receipt_number": submission.receipt_number,
        "title": redact_pii(complaint.redacted_title or complaint.title).text,
        "content": redact_pii(complaint.redacted_content or complaint.content).text,
        "location": redact_pii(
            complaint.redacted_location_text or complaint.location_text or ""
        ).text,
        "created_at": korean_time(complaint.created_at),
        "state": state,
        "status": {"received": "접수 완료", "checking": "확인 중", "answered": "답변 등록"}[state],
        "answer": redact_pii(reply.answer_text).text if reply else None,
        "answered_at": korean_time(reply.published_at) if reply else None,
    }


def korean_time(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return (aware + timedelta(hours=9)).strftime("%Y.%m.%d %H:%M")


def publish_reply(
    db: Session, complaint: Complaint, user: AuthenticatedUser, review_id: int
) -> PublishedReply:
    require_role(user, "reviewer")
    lock_complaint(db, complaint)
    latest_review = db.scalar(
        select(ReviewDecision)
        .where(ReviewDecision.complaint_id == complaint.id)
        .order_by(ReviewDecision.id.desc())
        .limit(1)
    )
    if (
        db.get(CitizenSubmission, complaint.id) is None
        or complaint.status != "reviewed"
        or latest_review is None
        or latest_review.id != review_id
        or latest_review.answer_draft != complaint.answer_draft
    ):
        raise ValueError("publication_requires_current_review")
    if redact_pii(latest_review.answer_draft).detected_types:
        raise ValueError("publication_contains_identifiers")
    existing = db.scalar(select(PublishedReply).where(PublishedReply.review_id == review_id))
    if existing:
        db.commit()
        return existing
    reply = PublishedReply(
        complaint_id=complaint.id,
        review_id=review_id,
        actor_id=user.username,
        answer_text=latest_review.answer_draft,
    )
    db.add(reply)
    db.flush()
    record_audit(
        db,
        complaint_id=complaint.id,
        action="citizen_reply_published",
        actor_type="officer",
        actor_id=user.username,
        details={"review_id": review_id, "reply_id": reply.id, "external_message_sent": False},
    )
    db.commit()
    return reply


class CitizenRateLimiter:
    """Bounded local limiter. Multi-process deployments need a shared limiter."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], tuple[int, float]] = {}
        self._lock = threading.Lock()

    def allow(self, action: str, client_address: str, limit: int) -> bool:
        now = time.monotonic()
        key = (action, digest(client_address))
        with self._lock:
            self._buckets = {k: v for k, v in self._buckets.items() if v[1] > now}
            if key not in self._buckets and len(self._buckets) >= 4096:
                return False
            count, until = self._buckets.get(key, (0, now + 60))
            if count >= limit:
                return False
            self._buckets[key] = (count + 1, until)
            return True
