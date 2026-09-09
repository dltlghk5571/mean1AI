"""Authenticated, transient report inspection. No catalog writes or external requests."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.complaints import _require_action
from app.services.auth import get_authenticated_user
from app.services.collection_review import MAX_REPORT_BYTES, inspect_report
from app.services.collection_sources import CollectionError

router = APIRouter(tags=["collection reports"])
PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/staff/source-reports", response_class=HTMLResponse, include_in_schema=False)
def report_page(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="source_reports.html",
        headers=PRIVATE_HEADERS,
        context={
            "current_user": get_authenticated_user(request),
            "active_filter": "source_reports",
        },
    )


@router.post("/api/v1/source-reports/preview")
async def preview_report(request: Request) -> JSONResponse:
    _require_action(request, "triage_officer", "reviewer", "auditor")
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "report_json_required", headers=PRIVATE_HEADERS)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REPORT_BYTES:
            raise HTTPException(413, "report_too_large", headers=PRIVATE_HEADERS)
        body.extend(chunk)
    try:
        result = await run_in_threadpool(inspect_report, bytes(body))
    except CollectionError as exc:
        # Error codes contain no input values, filenames, document text or model prompts.
        raise HTTPException(422, str(exc), headers=PRIVATE_HEADERS) from None
    return JSONResponse(result, headers=PRIVATE_HEADERS)


@router.get("/api/v1/source-reports/example")
def example_report(request: Request) -> JSONResponse:
    path = request.app.state.settings.package_dir / "data" / "collection_report_demo.json"
    return JSONResponse(inspect_report(path.read_bytes()), headers=PRIVATE_HEADERS)
