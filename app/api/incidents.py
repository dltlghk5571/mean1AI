"""Authenticated staff forms for shared incidents. No anonymous incident index is exposed."""

import json
import logging
from contextlib import suppress
from typing import Annotated, Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.pages import CATEGORY_LABELS, _require_form_action
from app.api.pages import STATUS_LABELS as COMPLAINT_STATUS_LABELS
from app.database import get_db
from app.services import incidents
from app.services.auth import get_authenticated_user
from app.services.citizen import korean_time

router = APIRouter(include_in_schema=False)
DbSession = Annotated[Session, Depends(get_db)]
logger = logging.getLogger(__name__)


async def _read_data(request: Request) -> dict[str, str] | JSONResponse:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 32_000:
            return JSONResponse({"message": "입력한 내용이 너무 깁니다."}, status_code=413)
    try:
        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=8)
            data = dict(pairs)
            if len(data) != len(pairs):
                raise ValueError("Duplicate form fields")
        else:
            data = json.loads(body)
        if (
            not isinstance(data, dict)
            or len(data) > 8
            or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in data.items()
            )
        ):
            raise ValueError("Invalid incident form")
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"message": "입력 형식을 확인해 주세요."}, status_code=400)
    return data


def _page(request: Request, name: str, *, status: int = 200, **context: Any) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=name,
        status_code=status,
        context={
            "current_user": get_authenticated_user(request),
            "active_filter": "incidents",
            "incident_status_labels": incidents.STATUS_LABELS,
            "incident_action_labels": incidents.ACTION_LABELS,
            "status_labels": COMPLAINT_STATUS_LABELS,
            "category_labels": CATEGORY_LABELS,
            "korean_time": korean_time,
            "form": {},
            **context,
        },
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/staff/incidents")
def incident_queue(
    request: Request,
    db: DbSession,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    state: str = "active",
) -> HTMLResponse:
    state = "all" if state == "all" else "active"
    return _page(request, "incidents.html", queue=incidents.queue(db, page, state), state=state)


@router.get("/staff/incidents/prepare")
def prepare_incident(
    request: Request,
    db: DbSession,
    complaint_id: str,
    candidate_id: str,
) -> HTMLResponse:
    try:
        data = incidents.prepare(db, complaint_id, candidate_id)
    except incidents.IncidentError as exc:
        return _page(request, "incident_prepare.html", error=str(exc), status=exc.status)
    return _page(request, "incident_prepare.html", **data)


@router.get("/staff/incidents/{incident_id}")
def incident_detail(
    request: Request,
    db: DbSession,
    incident_id: str,
    event_page: Annotated[int, Query(ge=1, le=100_000)] = 1,
) -> HTMLResponse:
    try:
        data = incidents.detail(db, incident_id, event_page)
    except incidents.IncidentError as exc:
        return _page(request, "incident_detail.html", error=str(exc), status=exc.status)
    return _page(request, "incident_detail.html", **data)


def _save(
    db: Session, request: Request, action: str, data: dict[str, str], incident_id: str | None
) -> str:
    try:
        incident = incidents.execute(
            db, get_authenticated_user(request), action, data, incident_id=incident_id
        )
        db.commit()
        return incident.id
    except Exception:
        db.rollback()
        raise


async def _action(request: Request, db: Session, action: str, incident_id: str | None) -> Response:
    data = await _read_data(request)
    if isinstance(data, JSONResponse):
        return data
    _require_form_action(
        request,
        data.pop("csrf_token", None),
        *(
            ("reviewer",)
            if action in {"published", "withdrawn"}
            else ("triage_officer", "reviewer")
        ),
    )
    wants_json = "application/json" in request.headers.get("accept", "")
    try:
        saved_id = await run_in_threadpool(_save, db, request, action, data, incident_id)
    except incidents.IncidentError as exc:
        error, status = str(exc), exc.status
    except Exception:
        # SQL/validation failures may include form contents; log only the fixed event name.
        logger.warning("incident_action_failed")
        error, status = "저장 결과를 확인하지 못했습니다. 새로고침해 상태를 확인해 주세요.", 503
    else:
        target = f"/staff/incidents/{saved_id}"
        if wants_json:
            return JSONResponse({"redirect": target}, headers={"Cache-Control": "no-store"})
        return RedirectResponse(target, status_code=303)
    if wants_json:
        return JSONResponse(
            {"message": error}, status_code=status, headers={"Cache-Control": "no-store"}
        )
    context: dict[str, Any] = {}
    name = "incident_detail.html" if incident_id else "incident_prepare.html"
    with suppress(incidents.IncidentError):
        context = (
            incidents.detail(db, incident_id)
            if incident_id
            else incidents.prepare(db, data.get("complaint_id", ""), data.get("candidate_id", ""))
        )
    return _page(
        request, name, status=status, error=error, form=data, failed_action=action, **context
    )


@router.post("/staff/incidents/create")
async def create_incident(request: Request, db: DbSession) -> Response:
    return await _action(request, db, "created", None)


@router.post("/staff/incidents/{incident_id}/{action}")
async def change_incident(
    request: Request, db: DbSession, incident_id: str, action: str
) -> Response:
    return await _action(request, db, action, incident_id)
