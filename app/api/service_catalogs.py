"""Authenticated staging, review and withdrawal of immutable service snapshots."""

import json
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.complaints import _require_action
from app.database import get_db
from app.models import ServiceCatalogReview, ServiceCatalogVersion
from app.service_data_schemas import ServiceBundle
from app.services.service_catalog import active_catalog, review_catalog, stage_catalog

router = APIRouter(prefix="/api/v1/service-catalogs", tags=["service catalogs"])
DbSession = Annotated[Session, Depends(get_db)]


class CatalogDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "withdrawn"]
    review_due_at: date | None = None
    reason: str = Field(min_length=5, max_length=500)


async def read_json(request: Request) -> object:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2_000_000:
            raise HTTPException(413, "catalog_body_too_large")
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "invalid_catalog_json") from None


@router.get("")
def list_catalogs(db: DbSession) -> dict[str, object]:
    active = active_catalog(db)
    versions = db.scalars(
        select(ServiceCatalogVersion).order_by(ServiceCatalogVersion.created_at.desc()).limit(50)
    ).all()
    return {
        "active_version": active.version if active else None,
        "versions": [
            {
                "version": item.version,
                "content_hash": item.content_hash,
                "imported_by": item.imported_by,
                "created_at": item.created_at.isoformat(),
                "service_count": len(item.bundle["services"]),
            }
            for item in versions
        ],
    }


@router.post("", status_code=201)
async def import_catalog(request: Request, db: DbSession) -> dict[str, str]:
    actor = _require_action(request, "triage_officer", "reviewer")
    try:
        bundle = ServiceBundle.model_validate(await read_json(request))
        record = stage_catalog(db, bundle, actor)
        db.commit()
    except (ValidationError, ValueError):
        db.rollback()
        raise HTTPException(422, "invalid_or_conflicting_service_catalog") from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "catalog_changed_retry") from None
    return {"version": record.version, "content_hash": record.content_hash, "status": "staged"}


@router.get("/{version}")
def get_catalog(version: str, db: DbSession) -> dict[str, object]:
    record = db.get(ServiceCatalogVersion, version)
    if not record:
        raise HTTPException(404, "catalog_not_found")
    reviews = db.scalars(
        select(ServiceCatalogReview)
        .where(ServiceCatalogReview.version == version)
        .order_by(ServiceCatalogReview.id)
    ).all()
    return {
        "bundle": record.bundle,
        "content_hash": record.content_hash,
        "reviews": [
            {
                "id": event.id,
                "decision": event.decision,
                "actor_id": event.actor_id,
                "reason": event.reason,
                "review_due_at": str(event.review_due_at) if event.review_due_at else None,
            }
            for event in reviews
        ],
    }


@router.get("/candidates/{name}")
def get_bundled_candidate(name: str, request: Request) -> dict[str, object]:
    names = {
        "seongnam": "seongnam_service_candidates.json",
        "synthetic": "service_catalog_demo.json",
    }
    if name not in names:
        raise HTTPException(404, "candidate_not_found")
    path = request.app.state.settings.package_dir / "data" / names[name]
    bundle = ServiceBundle.model_validate_json(path.read_text(encoding="utf-8"))
    return bundle.model_dump(mode="json")


@router.post("/{version}/review")
async def decide_catalog(version: str, request: Request, db: DbSession) -> dict[str, object]:
    actor = _require_action(request, "reviewer")
    try:
        decision = CatalogDecision.model_validate(await read_json(request))
        event = review_catalog(db, version=version, actor=actor, **decision.model_dump())
        db.commit()
    except (ValidationError, ValueError):
        db.rollback()
        raise HTTPException(422, "catalog_review_rejected_check_version_usage_and_dates") from None
    return {"version": version, "review_id": event.id, "decision": event.decision}
