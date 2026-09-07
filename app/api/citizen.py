import json
import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.chat_schemas import ChatTurn
from app.database import get_db
from app.models import CitizenSession
from app.services import citizen, citizen_chat, citizen_followups
from app.services.citizen_photos import photo_summaries

router = APIRouter(include_in_schema=False)
DbSession = Annotated[Session, Depends(get_db)]
logger = logging.getLogger(__name__)

CATEGORIES = (
    {"key": "road", "name": "도로·보도", "hint": "깨진 보도블록, 불편한 길", "icon": "road"},
    {"key": "light", "name": "가로등", "hint": "어두운 골목, 꺼진 조명", "icon": "light"},
    {"key": "waste", "name": "청소·쓰레기", "hint": "쌓인 쓰레기, 거리 청소", "icon": "waste"},
    {"key": "park", "name": "공원·녹지", "hint": "공원 시설, 나무와 산책로", "icon": "park"},
    {
        "key": "other",
        "name": "그 밖의 불편",
        "hint": "어떤 분야인지 몰라도 괜찮아요",
        "icon": "chat",
    },
)


def _page(request: Request, name: str, *, status: int = 200, **context: object) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status
    )


def _session_page(request: Request, db: Session, name: str, **context: object) -> HTMLResponse:
    session = citizen.read_session(db, request.cookies.get(citizen.COOKIE_NAME))
    token = None
    if session is None:
        session, token = citizen.create_session(db)
    if name == "citizen_lookup.html":
        context["records"] = [
            citizen.public_record(db, item) for item in citizen.accessible_submissions(db, session)
        ]
    response = request.app.state.templates.TemplateResponse(
        request=request, name=name, context={"csrf_token": session.csrf_token, **context}
    )
    if token:
        response.set_cookie(
            citizen.COOKIE_NAME,
            token,
            max_age=citizen.SESSION_TTL,
            httponly=True,
            secure=request.app.state.settings.app_env == "production",
            samesite="strict",
            path="/",
        )
    return response


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return _page(request, "citizen_home.html", categories=CATEGORIES, active_page="home")


@router.get("/minwon/new", response_class=HTMLResponse)
def new_complaint(request: Request, db: DbSession) -> HTMLResponse:
    return _session_page(
        request,
        db,
        "citizen_chat.html",
        active_page="new",
        agent_demo=request.app.state.agent_executor is not None,
        club_mode=request.app.state.settings.chat_provider == "club",
    )


@router.get("/minwon/form", response_class=HTMLResponse)
def complaint_form(request: Request, db: DbSession, topic: str = "") -> HTMLResponse:
    category = next((item for item in CATEGORIES if item["key"] == topic), None)
    return _session_page(
        request,
        db,
        "citizen_new.html",
        active_page="new",
        category=category,
        request_key=str(uuid4()),
    )


@router.get("/minwon/lookup", response_class=HTMLResponse)
def lookup_page(request: Request, db: DbSession) -> HTMLResponse:
    return _session_page(request, db, "citizen_lookup.html", active_page="lookup")


def _private_page(
    request: Request, db: Session, complaint_id: str, *, receipt: bool, followup_page: int = 1
) -> HTMLResponse:
    token = request.cookies.get(citizen.COOKIE_NAME)
    session = citizen.read_session(db, token)
    submission = citizen.get_accessible(db, session, complaint_id)
    if submission is None:
        return _page(request, "citizen_unavailable.html", status=404)
    owner = session is not None and session.token_hash == submission.owner_session_hash
    return _page(
        request,
        "citizen_receipt.html" if receipt else "citizen_detail.html",
        record=citizen.public_record(db, submission),
        photos=photo_summaries(db, complaint_id),
        lookup_code=citizen.lookup_code(token, submission.request_key)
        if receipt and owner and token
        else None,
        is_owner=owner,
        csrf_token=session.csrf_token if session else None,
        followup_key=str(uuid4()),
        followups=citizen_followups.history(db, complaint_id, followup_page)
        if not receipt
        else None,
        active_page="lookup",
    )


@router.get("/minwon/{complaint_id}/receipt", response_class=HTMLResponse)
def receipt_page(complaint_id: str, request: Request, db: DbSession) -> HTMLResponse:
    return _private_page(request, db, complaint_id, receipt=True)


@router.get("/minwon/{complaint_id}", response_class=HTMLResponse)
def detail_page(
    complaint_id: str,
    request: Request,
    db: DbSession,
    followup_page: Annotated[int, Query(ge=1, le=100_000)] = 1,
) -> HTMLResponse:
    return _private_page(request, db, complaint_id, receipt=False, followup_page=followup_page)


def _error(message: str, status: int, errors: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"message": message, "errors": errors or {}}, status_code=status)


def _action_session(
    request: Request, db: Session, action: str, limit: int
) -> CitizenSession | JSONResponse:
    session = citizen.read_session(db, request.cookies.get(citizen.COOKIE_NAME))
    if not citizen.valid_csrf(session, request.headers.get("X-Citizen-CSRF")):
        return _error(
            "접수 세션을 확인할 수 없습니다. 새 탭에서 신청 화면을 다시 열어 주세요.", 403
        )
    address = request.client.host if request.client else "unknown"
    if not request.app.state.citizen_limiter.allow(action, address, limit):
        response = _error("요청이 많습니다. 1분 후 다시 시도해 주세요.", 429)
        response.headers["Retry-After"] = "60"
        return response
    assert session is not None
    return session


async def _read_data(request: Request) -> dict[str, str] | JSONResponse:
    # Limit even chunked requests, and never echo raw input in validation errors.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 160_000:
            return _error("입력한 내용이 너무 깁니다. 글자 수를 확인해 주세요.", 413)
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return _error("입력 정보를 읽을 수 없습니다. 다시 시도해 주세요.", 400)
    if (
        not isinstance(data, dict)
        or len(data) > 8
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in data.items())
    ):
        return _error("입력 형식을 확인해 주세요.", 400)
    return data


@router.post("/minwon/chat/open")
async def open_chat(request: Request, db: DbSession) -> Response:
    session = _action_session(request, db, "chat_open", 30)
    if isinstance(session, JSONResponse):
        return session
    return JSONResponse(await run_in_threadpool(citizen_chat.open_chat, db, session))


@router.post("/minwon/{complaint_id}/follow-ups")
async def add_followup(complaint_id: str, request: Request, db: DbSession) -> Response:
    session = _action_session(request, db, "followup", 5)
    if isinstance(session, JSONResponse):
        return session
    data = await _read_data(request)
    if isinstance(data, JSONResponse):
        return data
    try:
        followup = await run_in_threadpool(
            citizen_followups.add_followup, db, session, complaint_id, data
        )
    except citizen_followups.FollowUpError as exc:
        db.rollback()
        return _error(str(exc), exc.status)
    except Exception:
        db.rollback()
        logger.warning("citizen_followup_failed")
        return _error("저장 결과를 확인하지 못했어요. 같은 내용으로 다시 시도해 주세요.", 503)
    return JSONResponse(
        {"redirect": f"/minwon/{complaint_id}?saved={followup.id}#followup-{followup.id}"}
    )


@router.post("/minwon/chat/turn")
async def chat_turn(request: Request, db: DbSession) -> Response:
    session = _action_session(request, db, "chat_turn", 30)
    if isinstance(session, JSONResponse):
        return session
    data = await _read_data(request)
    if isinstance(data, JSONResponse):
        return data
    try:
        turn = ChatTurn.model_validate(data)
    except ValidationError:
        return _error("입력 형식이나 글자 수를 확인해 주세요.", 422)
    if turn.action == "confirm":
        address = request.client.host if request.client else "unknown"
        if not request.app.state.citizen_limiter.allow("submit", address, 5):
            return _error("접수 요청이 많아요. 1분 후 다시 시도해 주세요.", 429)
    try:
        result = await run_in_threadpool(
            citizen_chat.advance_chat,
            db,
            session,
            request.cookies[citizen.COOKIE_NAME],
            turn,
            request.app.state.chat_provider,
            request.app.state.pipeline,
            request.app.state.agent_executor,
        )
    except citizen_chat.ChatError as exc:
        return JSONResponse(
            {"message": str(exc), "errors": {}, "urgent": exc.urgent}, status_code=exc.status
        )
    except citizen.CitizenValidationError as exc:
        return _error("접수할 내용을 확인해 주세요.", 422, exc.errors)
    except Exception:
        logger.warning("citizen_chat_failed")
        return _error("요청을 완료하지 못했어요. 같은 내용으로 다시 시도해 주세요.", 503)
    return JSONResponse(result)


@router.post("/minwon/preview")
async def preview(request: Request, db: DbSession) -> Response:
    session = _action_session(request, db, "preview", 30)
    if isinstance(session, JSONResponse):
        return session
    data = await _read_data(request)
    if isinstance(data, JSONResponse):
        return data
    try:
        payload = citizen.validate_submission(data, submitting=False)
    except citizen.CitizenValidationError as exc:
        return _error("입력한 내용을 확인해 주세요.", 422, exc.errors)
    return JSONResponse(citizen.preview_submission(payload))


@router.post("/minwon/submit")
async def submit_complaint(request: Request, db: DbSession) -> Response:
    session = _action_session(request, db, "submit", 5)
    if isinstance(session, JSONResponse):
        return session
    data = await _read_data(request)
    if isinstance(data, JSONResponse):
        return data
    try:
        submission = await run_in_threadpool(
            citizen.submit,
            db,
            request.app.state.pipeline,
            session,
            request.cookies[citizen.COOKIE_NAME],
            data,
        )
    except citizen.CitizenValidationError as exc:
        return _error("입력한 내용을 확인해 주세요.", 422, exc.errors)
    except Exception:
        # SQL exceptions can contain bound raw complaint data: no exception text or traceback.
        logger.warning("citizen_intake_failed")
        return _error("접수를 완료하지 못했습니다. 입력 내용은 유지되니 다시 시도해 주세요.", 503)
    return JSONResponse({"redirect": f"/minwon/{submission.complaint_id}/receipt"})


@router.post("/minwon/lookup")
async def lookup(request: Request, db: DbSession) -> Response:
    session = _action_session(request, db, "lookup", 10)
    if isinstance(session, JSONResponse):
        return session
    data = await _read_data(request)
    if isinstance(data, JSONResponse):
        return data
    receipt = data.get("receipt_number", "")
    code = data.get("lookup_code", "")
    if len(receipt) > 40 or len(code) > 100:
        return _error("접수번호 또는 조회 코드를 확인해 주세요.", 400)
    submission = await run_in_threadpool(citizen.grant_lookup, db, session, receipt, code)
    if submission is None:
        return _error("접수번호 또는 조회 코드를 확인해 주세요.", 400)
    return JSONResponse({"redirect": f"/minwon/{submission.complaint_id}"})
