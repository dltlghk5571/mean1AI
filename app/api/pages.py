from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Complaint, Department
from app.schemas import Channel, ComplaintApproval, ComplaintCreate
from app.services.pipeline import ComplaintPipeline

router = APIRouter(include_in_schema=False)
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
        raise HTTPException(status_code=404, detail="민원을 찾을 수 없습니다.")
    return complaint


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: DbSession) -> HTMLResponse:
    complaints = list(
        db.scalars(select(Complaint).order_by(Complaint.created_at.desc()).limit(12)).all()
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"complaints": complaints},
    )


@router.post("/submit")
def submit_complaint(
    request: Request,
    db: DbSession,
    title: Annotated[str, Form(min_length=2, max_length=200)],
    content: Annotated[str, Form(min_length=5, max_length=20_000)],
    location_text: Annotated[str | None, Form(max_length=300)] = None,
    channel: Annotated[Channel, Form()] = Channel.WEB,
) -> RedirectResponse:
    payload = ComplaintCreate(
        title=title,
        content=content,
        location_text=location_text or None,
        channel=channel,
    )
    complaint = _get_pipeline(request).create_and_process(db, payload)
    return RedirectResponse(url=f"/complaints/{complaint.id}", status_code=303)


@router.get("/complaints/{complaint_id}", response_class=HTMLResponse)
def complaint_detail(complaint_id: str, request: Request, db: DbSession) -> HTMLResponse:
    complaint = _get_complaint(db, complaint_id)
    departments = list(
        db.scalars(
            select(Department).where(Department.active.is_(True)).order_by(Department.name)
        ).all()
    )
    department_map = {department.id: department for department in departments}
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="complaint_detail.html",
        context={
            "complaint": complaint,
            "departments": departments,
            "department_map": department_map,
        },
    )


@router.post("/complaints/{complaint_id}/approve")
def approve_complaint_form(
    complaint_id: str,
    request: Request,
    db: DbSession,
    department_id: Annotated[str, Form(min_length=1, max_length=64)],
    answer_draft: Annotated[str, Form(min_length=1, max_length=20_000)],
    actor_id: Annotated[str, Form(min_length=1, max_length=120)],
) -> RedirectResponse:
    complaint = _get_complaint(db, complaint_id)
    approval = ComplaintApproval(
        department_id=department_id,
        answer_draft=answer_draft,
        actor_id=actor_id,
    )
    try:
        _get_pipeline(request).approve(db, complaint, approval)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/complaints/{complaint_id}", status_code=303)


@router.post("/complaints/{complaint_id}/reprocess")
def reprocess_complaint_form(
    complaint_id: str,
    request: Request,
    db: DbSession,
) -> RedirectResponse:
    complaint = _get_complaint(db, complaint_id)
    _get_pipeline(request).reprocess(db, complaint)
    return RedirectResponse(url=f"/complaints/{complaint_id}", status_code=303)
