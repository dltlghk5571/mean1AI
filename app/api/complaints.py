from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    AIProcessingJob,
    Complaint,
    ComplaintLocationReview,
    GroundedDraftRecord,
    ReviewDecision,
)
from app.schemas import (
    AIProcessingRead,
    ComplaintApproval,
    ComplaintApprovalRequest,
    ComplaintCreate,
    ComplaintDetail,
    ComplaintRead,
    DuplicateCandidateRead,
    DuplicateDecisionRequest,
    GroundedDraftRead,
    LocationReviewRead,
    ReviewDecisionRead,
)
from app.services.auth import (
    AuthenticatedUser,
    UserRole,
    get_authenticated_user,
    require_csrf,
    require_role,
)
from app.services.duplicates import (
    confirm_location,
    decide_duplicate_candidate,
    list_duplicate_candidates,
)
from app.services.pipeline import ComplaintPipeline

router = APIRouter(prefix="/api/v1/complaints", tags=["complaints"])
DbSession = Annotated[Session, Depends(get_db)]


def _get_pipeline(request: Request) -> ComplaintPipeline:
    return request.app.state.pipeline


def _require_action(request: Request, *roles: UserRole) -> AuthenticatedUser:
    user = get_authenticated_user(request)
    try:
        require_role(user, *roles)
        require_csrf(user, request.headers.get("X-CSRF-Token"))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return user


def _get_complaint(db: Session, complaint_id: str) -> Complaint:
    complaint = db.scalar(
        select(Complaint)
        .options(selectinload(Complaint.audit_events))
        .where(Complaint.id == complaint_id)
    )
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found")
    return complaint


@router.post("", response_model=ComplaintRead, status_code=status.HTTP_201_CREATED)
def create_complaint(
    payload: ComplaintCreate,
    request: Request,
    db: DbSession,
) -> Complaint:
    user = _require_action(request, "triage_officer", "reviewer")
    return _get_pipeline(request).create_and_process(db, payload, actor_id=user.username)


@router.get("", response_model=list[ComplaintRead])
def list_complaints(db: DbSession, limit: int = 50) -> list[Complaint]:
    safe_limit = max(1, min(limit, 200))
    return list(
        db.scalars(select(Complaint).order_by(Complaint.created_at.desc()).limit(safe_limit)).all()
    )


@router.get("/{complaint_id}", response_model=ComplaintDetail)
def get_complaint(complaint_id: str, db: DbSession) -> Complaint:
    return _get_complaint(db, complaint_id)


@router.get("/{complaint_id}/grounding", response_model=GroundedDraftRead)
def get_grounded_draft(complaint_id: str, db: DbSession) -> GroundedDraftRecord:
    _get_complaint(db, complaint_id)
    grounding = db.get(GroundedDraftRecord, complaint_id)
    if grounding is None:
        raise HTTPException(
            status_code=404,
            detail="Grounding record not found; reprocess complaint",
        )
    return grounding


@router.get("/{complaint_id}/ai-processing", response_model=list[AIProcessingRead])
def get_ai_processing_history(complaint_id: str, db: DbSession) -> list[AIProcessingJob]:
    return _get_complaint(db, complaint_id).ai_jobs


@router.get("/{complaint_id}/reviews", response_model=list[ReviewDecisionRead])
def get_review_history(complaint_id: str, db: DbSession) -> list[ReviewDecision]:
    _get_complaint(db, complaint_id)
    return list(
        db.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.complaint_id == complaint_id)
            .order_by(ReviewDecision.id)
        ).all()
    )


@router.get("/{complaint_id}/location", response_model=LocationReviewRead)
def get_location_review(complaint_id: str, db: DbSession) -> ComplaintLocationReview:
    _get_complaint(db, complaint_id)
    location_review = db.get(ComplaintLocationReview, complaint_id)
    if location_review is None:
        raise HTTPException(
            status_code=404, detail="Location review not found; reprocess complaint"
        )
    return location_review


@router.post("/{complaint_id}/location/confirm", response_model=LocationReviewRead)
def confirm_complaint_location(
    complaint_id: str,
    request: Request,
    db: DbSession,
) -> ComplaintLocationReview:
    user = _require_action(request, "triage_officer", "reviewer")
    complaint = _get_complaint(db, complaint_id)
    try:
        location_review = confirm_location(db, complaint, actor_id=user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(location_review)
    return location_review


@router.get("/{complaint_id}/duplicate-candidates", response_model=list[DuplicateCandidateRead])
def get_duplicate_candidates(complaint_id: str, db: DbSession) -> list[DuplicateCandidateRead]:
    _get_complaint(db, complaint_id)
    return list_duplicate_candidates(db, complaint_id)


@router.post(
    "/{complaint_id}/duplicate-candidates/{candidate_complaint_id}/decision",
    response_model=DuplicateCandidateRead,
)
def review_duplicate_candidate(
    complaint_id: str,
    candidate_complaint_id: str,
    payload: DuplicateDecisionRequest,
    request: Request,
    db: DbSession,
) -> DuplicateCandidateRead:
    user = _require_action(request, "triage_officer", "reviewer")
    complaint = _get_complaint(db, complaint_id)
    try:
        decide_duplicate_candidate(
            db,
            complaint,
            candidate_complaint_id=candidate_complaint_id,
            decision=payload.decision,
            actor_id=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    candidates = list_duplicate_candidates(db, complaint_id)
    return next(
        candidate
        for candidate in candidates
        if candidate.candidate_complaint_id == candidate_complaint_id
    )


@router.post("/{complaint_id}/reprocess", response_model=ComplaintRead)
def reprocess_complaint(
    complaint_id: str,
    request: Request,
    db: DbSession,
    idempotency_key: Annotated[UUID | None, Header()] = None,
) -> Complaint:
    user = _require_action(request, "triage_officer", "reviewer")
    complaint = _get_complaint(db, complaint_id)
    return _get_pipeline(request).reprocess(
        db,
        complaint,
        request_key=str(idempotency_key) if idempotency_key else None,
        actor_id=user.username,
    )


@router.post("/{complaint_id}/approve", response_model=ComplaintRead)
def approve_complaint(
    complaint_id: str,
    payload: ComplaintApprovalRequest,
    request: Request,
    db: DbSession,
) -> Complaint:
    user = _require_action(request, "reviewer")
    complaint = _get_complaint(db, complaint_id)
    approval = ComplaintApproval(
        department_id=payload.department_id,
        answer_draft=payload.answer_draft,
        actor_id=user.username,
        actor_role=user.role,
    )
    try:
        return _get_pipeline(request).approve(db, complaint, approval)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
