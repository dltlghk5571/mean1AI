from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.schemas import SessionRead
from app.services.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    AuthManager,
    get_authenticated_user,
    require_csrf,
)

router = APIRouter(tags=["session"])

DEMO_LOGIN_HINTS = (
    {
        "username": "triage.demo",
        "password": "triage-demo-2026",
        "role": "분류 담당",
        "description": "접수·재분석·위치·중복 검토",
    },
    {
        "username": "review.demo",
        "password": "review-demo-2026",
        "role": "검토 승인",
        "description": "분류 담당 권한과 내부 검토 승인",
    },
    {
        "username": "audit.demo",
        "password": "audit-demo-2026",
        "role": "감사 조회",
        "description": "민원·감사·승인 이력 읽기 전용",
    },
)


def _auth_manager(request: Request) -> AuthManager:
    return request.app.state.auth_manager


def _safe_next_path(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


def _login_context(
    request: Request,
    *,
    error: str | None = None,
    username: str = "",
    next_path: str = "/",
) -> dict[str, object]:
    return {
        "request": request,
        "error": error,
        "username": username,
        "next_path": next_path,
        "demo_accounts": DEMO_LOGIN_HINTS,
    }


@router.get(
    "/login",
    response_class=HTMLResponse,
    response_model=None,
    include_in_schema=False,
)
def login_page(request: Request, next: str | None = None) -> HTMLResponse | RedirectResponse:
    manager = _auth_manager(request)
    if manager.read_session_token(request.cookies.get(SESSION_COOKIE_NAME)):
        return RedirectResponse(_safe_next_path(next), status_code=303)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context=_login_context(request, next_path=_safe_next_path(next)),
    )


@router.post(
    "/login",
    response_class=HTMLResponse,
    response_model=None,
    include_in_schema=False,
)
def login(
    request: Request,
    username: Annotated[str, Form(min_length=1, max_length=120)],
    password: Annotated[str, Form(min_length=1, max_length=200)],
    next_path: Annotated[str, Form(max_length=500)] = "/",
) -> HTMLResponse | RedirectResponse:
    manager = _auth_manager(request)
    user = manager.authenticate(username, password)
    safe_next = _safe_next_path(next_path)
    if user is None:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="login.html",
            context=_login_context(
                request,
                error="아이디 또는 비밀번호를 확인해 주세요.",
                username=username,
                next_path=safe_next,
            ),
            status_code=401,
        )

    response = RedirectResponse(safe_next, status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=manager.create_session_token(user),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=request.app.state.settings.app_env == "production",
        samesite="strict",
        path="/",
    )
    return response


@router.post("/logout", include_in_schema=False)
def logout(
    request: Request,
    csrf_token: Annotated[str, Form(min_length=1, max_length=200)],
) -> RedirectResponse:
    user = get_authenticated_user(request)
    try:
        require_csrf(user, csrf_token)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", samesite="strict")
    return response


@router.get("/api/v1/session", response_model=SessionRead)
def session(request: Request) -> SessionRead:
    user = get_authenticated_user(request)
    return SessionRead(
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        role_label=user.role_label,
        permissions=user.permissions,
        csrf_token=user.csrf_token,
        expires_at=user.expires_at,
    )
