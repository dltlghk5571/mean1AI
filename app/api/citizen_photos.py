"""Photos have their own bounded confirmation envelope, outside every model context."""

import asyncio
import logging
from threading import BoundedSemaphore
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.citizen import _action_session, _error
from app.database import get_db
from app.models import CitizenPhoto
from app.services import citizen, citizen_chat
from app.services.citizen_photos import MAX_BODY_BYTES, PhotoError, prepare_confirmation

router = APIRouter(include_in_schema=False)
DbSession = Annotated[Session, Depends(get_db)]
logger = logging.getLogger(__name__)
photo_slots = BoundedSemaphore(2)


@router.post("/minwon/chat/confirm-with-photos")
async def confirm_with_photos(request: Request, db: DbSession) -> Response:
    session = _action_session(request, db, "submit", 5)
    if isinstance(session, JSONResponse):
        return session
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        return _error("사진 접수 형식을 확인해 주세요.", 415)
    if not photo_slots.acquire(blocking=False):
        response = _error("사진 접수가 몰리고 있어요. 잠시 후 다시 시도해 주세요.", 429)
        response.headers["Retry-After"] = "5"
        return response
    try:
        body = bytearray()
        async with asyncio.timeout(20):
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    return _error("사진은 최대 3장, 한 장당 5MB까지 첨부할 수 있어요.", 413)
                body.extend(chunk)
        turn, photos = await run_in_threadpool(prepare_confirmation, bytes(body))
        result = await run_in_threadpool(
            citizen_chat.advance_chat,
            db,
            session,
            request.cookies[citizen.COOKIE_NAME],
            turn,
            request.app.state.chat_provider,
            request.app.state.pipeline,
            request.app.state.agent_executor,
            photos,
        )
        return JSONResponse(result)
    except TimeoutError:
        return _error("사진 전송 시간이 초과됐어요. 연결을 확인하고 다시 시도해 주세요.", 408)
    except ValidationError:
        return _error("사진 형식·개수와 최종 접수 동의를 확인해 주세요.", 422)
    except PhotoError as exc:
        return _error(str(exc), 422)
    except citizen_chat.ChatError as exc:
        return _error(str(exc), exc.status)
    except citizen.CitizenValidationError as exc:
        return _error("접수할 내용을 확인해 주세요.", 422, exc.errors)
    except Exception:
        db.rollback()
        logger.warning("citizen_photo_confirmation_failed")
        return _error("사진 접수를 완료하지 못했어요. 같은 내용으로 다시 시도해 주세요.", 503)
    finally:
        photo_slots.release()


def photo_response(db: Session, complaint_id: str, photo_id: str) -> Response:
    photo = db.scalar(
        select(CitizenPhoto).where(
            CitizenPhoto.id == photo_id, CitizenPhoto.complaint_id == complaint_id
        )
    )
    if photo is None:
        raise HTTPException(404, "photo_not_found")
    return Response(
        photo.content,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="complaint-photo.jpg"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/minwon/{complaint_id}/photos/{photo_id}")
def citizen_photo(complaint_id: str, photo_id: str, request: Request, db: DbSession) -> Response:
    session = citizen.read_session(db, request.cookies.get(citizen.COOKIE_NAME))
    if citizen.get_accessible(db, session, complaint_id) is None:
        raise HTTPException(404, "photo_not_found")
    return photo_response(db, complaint_id, photo_id)


@router.get("/api/v1/complaints/{complaint_id}/photos/{photo_id}")
def officer_photo(complaint_id: str, photo_id: str, db: DbSession) -> Response:
    # Existing officer middleware protects this route, like the complaint detail itself.
    return photo_response(db, complaint_id, photo_id)
