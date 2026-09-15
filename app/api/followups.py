"""Officer follow-up queue and explicit publication; citizen writes live in citizen.py."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.citizen import _error, _read_data
from app.api.pages import _require_form_action
from app.database import get_db
from app.services.auth import get_authenticated_user
from app.services.citizen_followups import FollowUpError, pending_queue, publish_followup_reply

router = APIRouter(include_in_schema=False)
DbSession = Annotated[Session, Depends(get_db)]
logger = logging.getLogger(__name__)


@router.get("/staff/follow-ups", response_class=HTMLResponse)
def queue(
    request: Request,
    db: DbSession,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="followup_queue.html",
        context={
            "current_user": get_authenticated_user(request),
            "active_filter": "followups",
            "followups": pending_queue(db, page),
        },
    )


@router.post("/complaints/{complaint_id}/follow-ups/{followup_id}/reply")
async def publish(
    complaint_id: str,
    followup_id: str,
    request: Request,
    db: DbSession,
) -> Response:
    user = _require_form_action(request, None, "reviewer")
    data = await _read_data(request)
    if isinstance(data, JSONResponse):
        return data
    try:
        await run_in_threadpool(publish_followup_reply, db, user, complaint_id, followup_id, data)
    except FollowUpError as exc:
        db.rollback()
        return _error(str(exc), exc.status)
    except Exception:
        db.rollback()
        logger.warning("followup_reply_failed")
        return _error("저장 결과를 확인하지 못했습니다. 같은 답변으로 다시 시도해 주세요.", 503)
    return JSONResponse({"saved": True})
