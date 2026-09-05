"""Durable local SQLite queue. Callers own the transaction and its single commit."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AIProcessingJob, AIProcessingRequest, Complaint
from app.schemas import ComplaintStatus
from app.services.audit import record_audit
from app.services.classifier import DepartmentCatalog
from app.services.pii import redact_pii

ACTIVE_STATES = ("queued", "processing")
ERROR_CODES = {
    "provider_error",
    "processing_error",
    "lease_expired",
    "catalog_changed",
    "configuration_changed",
    "input_changed",
    "human_review_superseded",
    "safety_review_required",
}


def validate_local_queue(settings: Settings) -> None:
    url = make_url(settings.database_url)
    if (
        settings.app_env not in {"development", "test"}
        or url.get_backend_name() != "sqlite"
        or url.host
        or not url.database
        or url.database == ":memory:"
        or url.database.startswith(("file:", "//", "\\\\"))
        or url.query
    ):
        raise ValueError("Deferred AI requires a local file-backed SQLite development database")


def input_fingerprint(complaint: Complaint) -> str:
    fields = [complaint.title, complaint.content, complaint.location_text or ""]
    redacted = [redact_pii(value).text for value in fields]
    return hashlib.sha256(json.dumps(redacted, ensure_ascii=False).encode("utf-8")).hexdigest()


def lock_complaint(db: Session, complaint: Complaint) -> None:
    # SQLite ignores SELECT FOR UPDATE. A no-op UPDATE serializes local projection writers.
    db.execute(
        update(Complaint)
        .where(Complaint.id == complaint.id)
        .values(updated_at=Complaint.updated_at)
    )
    db.refresh(complaint)


def existing_request(db: Session, complaint_id: str, request_key: str) -> AIProcessingJob | None:
    """Caller holds the complaint lock; remember coalesced keys for later HTTP retries."""
    request = db.get(AIProcessingRequest, (complaint_id, request_key))
    if request is not None:
        return db.get(AIProcessingJob, request.job_id)
    job = db.scalar(
        select(AIProcessingJob)
        .where(
            AIProcessingJob.complaint_id == complaint_id,
            or_(
                AIProcessingJob.request_key == request_key,
                AIProcessingJob.state.in_(ACTIVE_STATES),
            ),
        )
        .order_by(AIProcessingJob.id.desc())
        .limit(1)
    )
    if job is not None:
        db.add(
            AIProcessingRequest(complaint_id=complaint_id, request_key=request_key, job_id=job.id)
        )
        db.flush()
    return job


def enqueue(
    db: Session,
    complaint: Complaint,
    *,
    settings: Settings,
    catalog: DepartmentCatalog,
    request_key: str,
    actor_id: str | None,
    now: datetime,
) -> AIProcessingJob:
    validate_local_queue(settings)
    if not settings.ai_deferred_enabled or settings.ai_provider == "rules":
        raise ValueError("Deferred AI queue is disabled")
    lock_complaint(db, complaint)
    existing = existing_request(db, complaint.id, request_key)
    if existing is not None:
        return existing
    if complaint.reviewed_at is not None:
        raise ValueError("Reviewed complaints require explicit reprocessing before enqueue")
    require_human_review(complaint)
    job = AIProcessingJob(
        complaint_id=complaint.id,
        active_complaint_id=complaint.id,
        request_key=request_key,
        state="queued",
        attempts=0,
        max_attempts=settings.ai_queue_max_attempts,
        retry_seconds=settings.ai_queue_retry_seconds,
        provider=settings.ai_provider,
        model=settings.openai_model,
        catalog_version=catalog.catalog_version,
        source_sha256=catalog.source_sha256,
        input_sha256=input_fingerprint(complaint),
        available_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()
    db.add(AIProcessingRequest(complaint_id=complaint.id, request_key=request_key, job_id=job.id))
    db.flush()
    record_audit(
        db,
        complaint_id=complaint.id,
        action="ai_job_enqueued",
        actor_type="officer" if actor_id else "system",
        actor_id=actor_id,
        details={"job_id": job.id, "state": "queued", "max_attempts": job.max_attempts},
    )
    db.expire(complaint, ["ai_jobs"])
    return job


@dataclass(frozen=True)
class Claim:
    job_id: int
    complaint_id: str
    token: str


def require_human_review(complaint: Complaint) -> None:
    if complaint.reviewed_at is not None:
        return
    complaint.requires_human_review = True
    complaint.assigned_department_id = None
    complaint.status = (
        ComplaintStatus.URGENT_REVIEW.value
        if complaint.urgency != "normal"
        else ComplaintStatus.NEEDS_REVIEW.value
    )


def _event(
    db: Session,
    job: AIProcessingJob,
    action: str,
    *,
    actor_type: str = "worker",
    actor_id: str = "local-ai-worker",
) -> None:
    record_audit(
        db,
        complaint_id=job.complaint_id,
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        details={
            "job_id": job.id,
            "state": job.state,
            "attempt": job.attempts,
            "max_attempts": job.max_attempts,
            "reason": job.last_error_code,
        },
    )


def fail(
    db: Session,
    claim: Claim,
    *,
    now: datetime,
    error_code: str,
    retryable: bool = True,
    expired: bool = False,
) -> bool:
    if error_code not in ERROR_CODES:
        error_code = "processing_error"
    job = db.get(AIProcessingJob, claim.job_id, populate_existing=True)
    if job is None:
        return False
    complaint = db.get(Complaint, job.complaint_id)
    assert complaint is not None
    lock_complaint(db, complaint)
    retry = retryable and job.attempts < job.max_attempts and complaint.reviewed_at is None
    job_id = db.scalar(
        update(AIProcessingJob)
        .where(
            AIProcessingJob.id == claim.job_id,
            AIProcessingJob.state == "processing",
            AIProcessingJob.claim_token == claim.token,
            (
                AIProcessingJob.lease_expires_at <= now
                if expired
                else AIProcessingJob.lease_expires_at > now
            ),
        )
        .values(
            state="queued" if retry else "failed",
            active_complaint_id=job.complaint_id if retry else None,
            claim_token=None,
            lease_expires_at=None,
            available_at=(
                now + timedelta(seconds=job.retry_seconds * 2 ** (job.attempts - 1))
                if retry
                else now
            ),
            last_error_code=error_code,
            updated_at=now,
            finished_at=None if retry else now,
        )
        .returning(AIProcessingJob.id)
        .execution_options(synchronize_session=False)
    )
    if job_id is None:
        return False
    db.refresh(job)
    require_human_review(complaint)
    _event(db, job, "ai_job_attempt_failed")
    if not retry:
        _event(db, job, "ai_job_failed")
    return True


def claim_next(db: Session, *, now: datetime, lease_seconds: int) -> Claim | None:
    expired = list(
        db.scalars(
            select(AIProcessingJob).where(
                AIProcessingJob.state == "processing", AIProcessingJob.lease_expires_at <= now
            )
        )
    )
    for job in expired:
        assert job.claim_token is not None
        fail(
            db,
            Claim(job.id, job.complaint_id, job.claim_token),
            now=now,
            error_code="lease_expired",
            expired=True,
        )
    next_id = (
        select(AIProcessingJob.id)
        .where(
            AIProcessingJob.state == "queued",
            AIProcessingJob.available_at <= now,
            AIProcessingJob.attempts < AIProcessingJob.max_attempts,
        )
        .order_by(AIProcessingJob.available_at, AIProcessingJob.id)
        .limit(1)
        .scalar_subquery()
    )
    token = str(uuid4())
    job_id = db.scalar(
        update(AIProcessingJob)
        .where(AIProcessingJob.id == next_id, AIProcessingJob.state == "queued")
        .values(
            state="processing",
            attempts=AIProcessingJob.attempts + 1,
            claim_token=token,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            updated_at=now,
        )
        .returning(AIProcessingJob.id)
        .execution_options(synchronize_session=False)
    )
    if job_id is None:
        return None
    claimed = db.get(AIProcessingJob, job_id, populate_existing=True)
    assert claimed is not None
    _event(db, claimed, "ai_job_claimed")
    return Claim(claimed.id, claimed.complaint_id, token)


def owns_claim(job: AIProcessingJob, claim: Claim, now: datetime) -> bool:
    deadline = job.lease_expires_at
    return (
        job.state == "processing"
        and job.claim_token == claim.token
        and deadline is not None
        and deadline.replace(tzinfo=UTC) > now
    )


def complete(db: Session, claim: Claim, *, now: datetime) -> bool:
    job_id = db.scalar(
        update(AIProcessingJob)
        .where(
            AIProcessingJob.id == claim.job_id,
            AIProcessingJob.state == "processing",
            AIProcessingJob.claim_token == claim.token,
            AIProcessingJob.lease_expires_at > now,
        )
        .values(
            state="completed",
            active_complaint_id=None,
            claim_token=None,
            lease_expires_at=None,
            last_error_code=None,
            updated_at=now,
            finished_at=now,
        )
        .returning(AIProcessingJob.id)
        .execution_options(synchronize_session=False)
    )
    if job_id is None:
        return False
    job = db.get(AIProcessingJob, job_id, populate_existing=True)
    assert job is not None
    _event(db, job, "ai_job_completed")
    return True


def supersede_for_review(
    db: Session, complaint: Complaint, *, now: datetime, actor_id: str
) -> None:
    jobs = list(
        db.scalars(
            update(AIProcessingJob)
            .where(
                AIProcessingJob.complaint_id == complaint.id,
                AIProcessingJob.state.in_(ACTIVE_STATES),
            )
            .values(
                state="failed",
                active_complaint_id=None,
                claim_token=None,
                lease_expires_at=None,
                last_error_code="human_review_superseded",
                updated_at=now,
                finished_at=now,
            )
            .returning(AIProcessingJob)
            .execution_options(populate_existing=True)
        )
    )
    for job in jobs:
        _event(db, job, "ai_job_failed", actor_type="officer", actor_id=actor_id)
