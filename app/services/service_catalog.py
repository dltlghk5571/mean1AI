"""Immutable imports and reviewer-controlled publication for agent retrieval."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, insert, literal, select
from sqlalchemy.orm import Session

from app.models import ServiceCatalogReview, ServiceCatalogVersion
from app.service_data_schemas import PublicService, ServiceBundle, SourceDocument
from app.services.auth import AuthenticatedUser, require_role
from app.services.knowledge import content_safety_violation
from app.services.pii import redact_pii


def bundle_hash(bundle: ServiceBundle) -> str:
    encoded = json.dumps(bundle.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def stage_catalog(
    db: Session, bundle: ServiceBundle, actor: AuthenticatedUser
) -> ServiceCatalogVersion:
    require_role(actor, "triage_officer", "reviewer")
    digest = bundle_hash(bundle)
    existing = db.get(ServiceCatalogVersion, bundle.version)
    if existing:
        if existing.content_hash != digest:
            raise ValueError("version_content_conflict")
        return existing
    # Source text may be official, but direct identifiers never enter the agent catalog.
    if redact_pii(bundle.model_dump_json()).detected_types:
        raise ValueError("catalog_contains_direct_identifiers")
    record = ServiceCatalogVersion(
        version=bundle.version,
        content_hash=digest,
        bundle=bundle.model_dump(mode="json"),
        imported_by=actor.username,
    )
    db.add(record)
    db.flush()
    db.add(
        ServiceCatalogReview(
            version=bundle.version,
            content_hash=digest,
            decision="staged",
            actor_id=actor.username,
            reason="Imported for review; not available to retrieval.",
        )
    )
    return record


def review_catalog(
    db: Session,
    *,
    version: str,
    content_hash: str,
    decision: str,
    review_due_at: date | None,
    reason: str,
    actor: AuthenticatedUser,
) -> ServiceCatalogReview:
    require_role(actor, "reviewer")
    db.flush()
    previous_event = db.scalar(select(func.coalesce(func.max(ServiceCatalogReview.id), 0)))
    record = db.get(ServiceCatalogVersion, version)
    if record is None or record.content_hash != content_hash:
        raise ValueError("catalog_version_mismatch")
    if decision not in {"approved", "withdrawn"}:
        raise ValueError("invalid_review_decision")
    if not 5 <= len(reason.strip()) <= 500 or redact_pii(reason).detected_types:
        raise ValueError("invalid_review_reason")
    today = datetime.now(UTC).date()
    if decision == "approved":
        if not review_due_at or not today <= review_due_at <= today + timedelta(days=365):
            raise ValueError("review_due_date_required_within_one_year")
        bundle = ServiceBundle.model_validate(record.bundle)
        if any(doc.retrieval_use != "allowed" for doc in bundle.documents):
            raise ValueError("retrieval_use_not_verified")
        if content_safety_violation(bundle.model_dump_json()):
            raise ValueError("unsafe_catalog_content")
    else:
        active = active_catalog(db)
        if active is None or active.version != version:
            raise ValueError("only_active_catalog_can_be_withdrawn")
    # One conditional INSERT prevents a concurrent withdrawal from hiding a newer publication.
    latest_event = select(func.coalesce(func.max(ServiceCatalogReview.id), 0)).scalar_subquery()
    event_id = db.execute(
        insert(ServiceCatalogReview)
        .from_select(
            ["version", "content_hash", "decision", "review_due_at", "reason", "actor_id"],
            select(
                literal(version),
                literal(content_hash),
                literal(decision),
                literal(review_due_at),
                literal(reason),
                literal(actor.username),
            ).where(latest_event == previous_event),
        )
        .returning(ServiceCatalogReview.id)
    ).scalar_one_or_none()
    if event_id is None:
        raise ValueError("catalog_review_changed_retry")
    event = db.get(ServiceCatalogReview, event_id)
    assert event is not None
    return event


@dataclass(frozen=True)
class ActiveCatalog:
    version: str
    review_id: int
    content_hash: str
    review_due_at: date
    bundle: ServiceBundle

    def services(self, as_of: date) -> list[PublicService]:
        return [
            item
            for item in self.bundle.services
            if (not item.effective_from or item.effective_from <= as_of)
            and (not item.effective_until or as_of <= item.effective_until)
        ]

    def document(self, document_id: str) -> SourceDocument:
        return next(item for item in self.bundle.documents if item.id == document_id)


def active_catalog(db: Session) -> ActiveCatalog | None:
    review = db.scalar(
        select(ServiceCatalogReview)
        .where(ServiceCatalogReview.decision.in_(["approved", "withdrawn"]))
        .order_by(ServiceCatalogReview.id.desc())
        .limit(1)
    )
    today = datetime.now(UTC).date()
    if (
        not review
        or review.decision != "approved"
        or not review.review_due_at
        or review.review_due_at < today
    ):
        return None
    record = db.get(ServiceCatalogVersion, review.version)
    if not record:
        raise ValueError("published_catalog_missing")
    bundle = ServiceBundle.model_validate(record.bundle)
    if bundle_hash(bundle) != review.content_hash or record.content_hash != review.content_hash:
        raise ValueError("published_catalog_hash_mismatch")
    return ActiveCatalog(
        record.version, review.id, record.content_hash, review.review_due_at, bundle
    )
