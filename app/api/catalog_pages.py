"""Officer review UI; all mutations use the existing authenticated catalog API."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ServiceCatalogReview, ServiceCatalogVersion
from app.service_data_schemas import ServiceBundle
from app.services.auth import get_authenticated_user
from app.services.service_catalog import active_catalog

router = APIRouter(include_in_schema=False)
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/staff/service-catalogs", response_class=HTMLResponse)
@router.get("/staff/service-catalogs/{version}", response_class=HTMLResponse)
def catalog_page(request: Request, db: DbSession, version: str | None = None) -> HTMLResponse:
    active = active_catalog(db)
    records = list(
        db.scalars(
            select(ServiceCatalogVersion)
            .order_by(ServiceCatalogVersion.created_at.desc())
            .limit(50)
        )
    )
    record = (
        db.get(ServiceCatalogVersion, version) if version else (records[0] if records else None)
    )
    if version and record is None:
        raise HTTPException(404, "catalog_not_found")
    bundle = ServiceBundle.model_validate(record.bundle) if record else None
    reviews = (
        list(
            db.scalars(
                select(ServiceCatalogReview)
                .where(ServiceCatalogReview.version == record.version)
                .order_by(ServiceCatalogReview.id.desc())
            )
        )
        if record
        else []
    )
    ready = bool(bundle and all(doc.retrieval_use == "allowed" for doc in bundle.documents))
    organizations = {item.id: item for item in bundle.organizations} if bundle else {}
    work = {item.id: item for item in bundle.work_assignments} if bundle else {}

    def organization_path(work_id: str) -> str:
        names: list[str] = []
        current: str | None = work[work_id].organization_id
        while current is not None:
            item = organizations[current]
            names.append(item.name)
            current = item.parent_id
        return " > ".join(reversed(names))

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="service_catalogs.html",
        context={
            "current_user": get_authenticated_user(request),
            "active_filter": "service_catalogs",
            "records": records,
            "record": record,
            "bundle": bundle,
            "reviews": reviews,
            "active": active,
            "usage_ready": ready,
            "today": datetime.now(UTC).date().isoformat(),
            "taxonomy": {item.id: item.label for item in bundle.taxonomy} if bundle else {},
            "organization_path": organization_path,
            "decision_labels": {
                "staged": "검수 대기 등록",
                "approved": "공개 승인",
                "withdrawn": "철회",
            },
        },
    )
