from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    Complaint,
    ComplaintLocationReview,
    Department,
    GroundedDraftRecord,
    ReviewDecision,
)
from app.schemas import (
    Channel,
    ComplaintApproval,
    ComplaintCreate,
    DuplicateDecision,
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

router = APIRouter(include_in_schema=False)
DbSession = Annotated[Session, Depends(get_db)]

STATUS_LABELS = {
    "received": "접수됨",
    "needs_review": "검토 대기",
    "urgent_review": "긴급 검토",
    "assigned": "배정 완료",
    "reviewed": "검토 완료",
}
URGENCY_LABELS = {"normal": "일반", "high": "높음", "critical": "긴급"}
CHANNEL_LABELS = {
    "web": "시민 웹",
    "sms": "문자 데모",
    "call_center": "콜센터 데모",
    "national_portal": "국민신문고 데모",
    "other": "기타",
}
CATEGORY_LABELS = {
    "streetlight": "가로등·보안등",
    "road_damage": "도로·보도",
    "waste": "청소·폐기물",
    "park": "공원·녹지",
    "traffic": "교통",
    "water_sewer": "상하수도",
    "welfare": "복지",
    "other": "소관 확인",
}
PII_LABELS = {
    "resident_registration_number": "주민등록번호",
    "mobile_phone": "휴대전화번호",
    "landline_phone": "유선전화번호",
    "email": "이메일",
}
AUDIT_LABELS = {
    "complaint_received": "민원 접수",
    "pii_redacted": "개인정보 비식별",
    "triage_completed": "자동 분류 완료",
    "knowledge_retrieved": "승인 지식 검색",
    "draft_grounding_validated": "문장별 근거 검증",
    "draft_grounding_invalidated": "수정 초안 근거 확인 필요",
    "location_normalized": "위치 문구 정규화",
    "location_confirmed": "담당자 위치 확인",
    "duplicate_candidates_scored": "유사 민원 후보 조회",
    "duplicate_candidate_confirmed": "중복 후보 확인",
    "duplicate_candidate_rejected": "중복 후보 제외",
    "human_review_approved": "담당자 검토 완료",
}

QUEUE_FILTERS: dict[str, tuple[str, ...]] = {
    "review": ("needs_review", "urgent_review"),
    "urgent": ("urgent_review",),
    "assigned": ("assigned",),
    "reviewed": ("reviewed",),
}


def _get_pipeline(request: Request) -> ComplaintPipeline:
    return request.app.state.pipeline


def _require_form_action(
    request: Request,
    csrf_token: str | None,
    *roles: UserRole,
) -> AuthenticatedUser:
    user = get_authenticated_user(request)
    try:
        require_role(user, *roles)
        require_csrf(user, csrf_token or request.headers.get("X-CSRF-Token"))
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
        raise HTTPException(status_code=404, detail="민원을 찾을 수 없습니다.")
    return complaint


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    db: DbSession,
    status: str | None = None,
) -> HTMLResponse:
    current_user = get_authenticated_user(request)
    active_filter = status if status in QUEUE_FILTERS else "all"
    complaint_query = select(Complaint).order_by(Complaint.created_at.desc())
    if active_filter != "all":
        complaint_query = complaint_query.where(Complaint.status.in_(QUEUE_FILTERS[active_filter]))
    complaints = list(db.scalars(complaint_query.limit(30)).all())

    count_rows = db.execute(
        select(Complaint.status, func.count(Complaint.id)).group_by(Complaint.status)
    ).all()
    status_counts = {row_status: int(count) for row_status, count in count_rows}
    total_count = sum(status_counts.values())
    review_count = status_counts.get("needs_review", 0) + status_counts.get("urgent_review", 0)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "complaints": complaints,
            "active_filter": active_filter,
            "stats": {
                "total": total_count,
                "review": review_count,
                "urgent": status_counts.get("urgent_review", 0),
                "reviewed": status_counts.get("reviewed", 0),
            },
            "status_labels": STATUS_LABELS,
            "channel_labels": CHANNEL_LABELS,
            "category_labels": CATEGORY_LABELS,
            "classifier_provider": getattr(
                request.app.state.pipeline.classifier, "provider_name", "unknown"
            ),
            "current_user": current_user,
        },
    )


@router.post("/submit")
def submit_complaint(
    request: Request,
    db: DbSession,
    title: Annotated[str, Form(min_length=2, max_length=200)],
    content: Annotated[str, Form(min_length=5, max_length=20_000)],
    location_text: Annotated[str | None, Form(max_length=300)] = None,
    channel: Annotated[Channel, Form()] = Channel.WEB,
    csrf_token: Annotated[str | None, Form(max_length=200)] = None,
) -> RedirectResponse:
    _require_form_action(request, csrf_token, "triage_officer", "reviewer")
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
    current_user = get_authenticated_user(request)
    complaint = _get_complaint(db, complaint_id)
    location_review = db.get(ComplaintLocationReview, complaint_id)
    grounding = db.get(GroundedDraftRecord, complaint_id)
    duplicate_candidates = list_duplicate_candidates(db, complaint_id)
    review_decisions = list(
        db.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.complaint_id == complaint_id)
            .order_by(ReviewDecision.id.desc())
        ).all()
    )
    departments = list(
        db.scalars(
            select(Department).where(Department.active.is_(True)).order_by(Department.name)
        ).all()
    )
    department_map = {department.id: department for department in departments}
    catalog = request.app.state.pipeline.catalog
    work_assignment_titles = {
        assignment.id: assignment.title
        for department in catalog.all_departments
        for assignment in department.work_assignments
    }
    candidate_assignment_labels: dict[str, str] = {}
    candidate_catalog_versions: set[str] = set()
    for candidate in complaint.candidate_departments:
        department_id = str(candidate.get("department_id", ""))
        candidate_catalog_version = candidate.get("catalog_version")
        if isinstance(candidate_catalog_version, str) and candidate_catalog_version:
            candidate_catalog_versions.add(candidate_catalog_version)
        assignment_ids = candidate.get("work_assignment_ids", [])
        if isinstance(assignment_ids, list):
            titles = [
                work_assignment_titles[assignment_id]
                for assignment_id in assignment_ids
                if isinstance(assignment_id, str) and assignment_id in work_assignment_titles
            ]
            candidate_assignment_labels[department_id] = " · ".join(titles)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="complaint_detail.html",
        context={
            "complaint": complaint,
            "location_review": location_review,
            "grounding": grounding,
            "duplicate_candidates": duplicate_candidates,
            "review_decisions": review_decisions,
            "departments": departments,
            "department_map": department_map,
            "catalog_version": (
                next(iter(candidate_catalog_versions))
                if len(candidate_catalog_versions) == 1
                else "기록 없음(이전 데이터)"
            ),
            "candidate_assignment_labels": candidate_assignment_labels,
            "status_labels": STATUS_LABELS,
            "urgency_labels": URGENCY_LABELS,
            "channel_labels": CHANNEL_LABELS,
            "category_labels": CATEGORY_LABELS,
            "pii_labels": PII_LABELS,
            "audit_labels": AUDIT_LABELS,
            "current_user": current_user,
        },
    )


@router.post("/complaints/{complaint_id}/approve")
def approve_complaint_form(
    complaint_id: str,
    request: Request,
    db: DbSession,
    department_id: Annotated[str, Form(min_length=1, max_length=64)],
    answer_draft: Annotated[str, Form(min_length=1, max_length=20_000)],
    csrf_token: Annotated[str | None, Form(max_length=200)] = None,
) -> RedirectResponse:
    user = _require_form_action(request, csrf_token, "reviewer")
    complaint = _get_complaint(db, complaint_id)
    approval = ComplaintApproval(
        department_id=department_id,
        answer_draft=answer_draft,
        actor_id=user.username,
        actor_role=user.role,
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
    csrf_token: Annotated[str | None, Form(max_length=200)] = None,
) -> RedirectResponse:
    _require_form_action(request, csrf_token, "triage_officer", "reviewer")
    complaint = _get_complaint(db, complaint_id)
    _get_pipeline(request).reprocess(db, complaint)
    return RedirectResponse(url=f"/complaints/{complaint_id}", status_code=303)


@router.post("/complaints/{complaint_id}/location/confirm")
def confirm_complaint_location_form(
    complaint_id: str,
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str | None, Form(max_length=200)] = None,
) -> RedirectResponse:
    user = _require_form_action(request, csrf_token, "triage_officer", "reviewer")
    complaint = _get_complaint(db, complaint_id)
    try:
        confirm_location(db, complaint, actor_id=user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return RedirectResponse(url=f"/complaints/{complaint_id}#duplicate-review", status_code=303)


@router.post("/complaints/{complaint_id}/duplicate-candidates/{candidate_complaint_id}/decision")
def review_duplicate_candidate_form(
    complaint_id: str,
    candidate_complaint_id: str,
    request: Request,
    db: DbSession,
    decision: Annotated[DuplicateDecision, Form()],
    csrf_token: Annotated[str | None, Form(max_length=200)] = None,
) -> RedirectResponse:
    user = _require_form_action(request, csrf_token, "triage_officer", "reviewer")
    complaint = _get_complaint(db, complaint_id)
    try:
        decide_duplicate_candidate(
            db,
            complaint,
            candidate_complaint_id=candidate_complaint_id,
            decision=decision,
            actor_id=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return RedirectResponse(url=f"/complaints/{complaint_id}#duplicate-review", status_code=303)
