from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Complaint
from app.schemas import ComplaintApproval, ComplaintCreate, ComplaintDetail, ComplaintRead
from app.services.pipeline import ComplaintPipeline

router = APIRouter(prefix="/api/v1/complaints", tags=["complaints"])
DbSession = Annotated[Session, Depends(get_db)]


def _get_pipeline(request: Request) -> ComplaintPipeline:
    return request.app.state.pipeline


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
    return _get_pipeline(request).create_and_process(db, payload)


@router.get("", response_model=list[ComplaintRead])
def list_complaints(db: DbSession, limit: int = 50) -> list[Complaint]:
    safe_limit = max(1, min(limit, 200))
    return list(
        db.scalars(select(Complaint).order_by(Complaint.created_at.desc()).limit(safe_limit)).all()
    )


@router.get("/{complaint_id}", response_model=ComplaintDetail)
def get_complaint(complaint_id: str, db: DbSession) -> Complaint:
    return _get_complaint(db, complaint_id)


@router.post("/{complaint_id}/reprocess", response_model=ComplaintRead)
def reprocess_complaint(
    complaint_id: str,
    request: Request,
    db: DbSession,
) -> Complaint:
    complaint = _get_complaint(db, complaint_id)
    return _get_pipeline(request).reprocess(db, complaint)


@router.post("/{complaint_id}/approve", response_model=ComplaintRead)
def approve_complaint(
    complaint_id: str,
    payload: ComplaintApproval,
    request: Request,
    db: DbSession,
) -> Complaint:
    complaint = _get_complaint(db, complaint_id)
    try:
        return _get_pipeline(request).approve(db, complaint, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
