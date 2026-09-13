"""Staff-only advisory comparison. Human candidate decisions remain in the complaint workflow."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.incidents import _page
from app.api.pages import _require_form_action
from app.database import get_db
from app.services.incident_comparison import IncidentComparisonRunner
from app.services.incidents import IncidentError

router = APIRouter(include_in_schema=False)
DbSession = Annotated[Session, Depends(get_db)]
logger = logging.getLogger(__name__)


def _view(
    request: Request,
    db: Session,
    complaint_id: str,
    candidate_id: str,
    *,
    error: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    runner: IncidentComparisonRunner = request.app.state.incident_comparator
    try:
        context = runner.view(db, complaint_id, candidate_id)
    except IncidentError as exc:
        return _page(request, "incident_comparison.html", error=str(exc), status=exc.status)
    return _page(request, "incident_comparison.html", error=error, status=status, **context)


@router.get("/staff/incident-comparisons/{complaint_id}/{candidate_id}")
def comparison_page(
    request: Request, db: DbSession, complaint_id: UUID, candidate_id: UUID
) -> HTMLResponse:
    return _view(request, db, str(complaint_id), str(candidate_id))


@router.post("/staff/incident-comparisons/{complaint_id}/{candidate_id}")
async def compare(
    request: Request,
    db: DbSession,
    complaint_id: UUID,
    candidate_id: UUID,
    expected_hash: Annotated[str, Form(pattern=r"^[a-f0-9]{64}$")],
    csrf_token: Annotated[str | None, Form(max_length=200)] = None,
) -> Response:
    user = _require_form_action(request, csrf_token, "triage_officer", "reviewer")
    runner: IncidentComparisonRunner = request.app.state.incident_comparator
    try:
        await run_in_threadpool(
            runner.run, user, str(complaint_id), str(candidate_id), expected_hash
        )
    except IncidentError as exc:
        return _view(
            request, db, str(complaint_id), str(candidate_id), error=str(exc), status=exc.status
        )
    except Exception:
        logger.warning("incident_comparison_failed")
        return _view(
            request,
            db,
            str(complaint_id),
            str(candidate_id),
            error="비교 결과를 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            status=503,
        )
    return RedirectResponse(
        f"/staff/incident-comparisons/{complaint_id}/{candidate_id}", status_code=303
    )
