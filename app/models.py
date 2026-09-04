from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(120), nullable=False, default="성남시 데모")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DepartmentCatalogVersion(Base):
    """Immutable metadata for one imported synthetic department catalog."""

    __tablename__ = "department_catalog_versions"

    catalog_version: Mapped[str] = mapped_column(String(80), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_label: Mapped[str] = mapped_column(String(160), nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fallback_department_id: Mapped[str] = mapped_column(String(64), nullable=False)
    department_count: Mapped[int] = mapped_column(Integer, nullable=False)
    work_assignment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    routing_rule_count: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class DepartmentCatalogEntry(Base):
    """Immutable department and work-assignment snapshot for one catalog version."""

    __tablename__ = "department_catalog_entries"

    catalog_version: Mapped[str] = mapped_column(
        ForeignKey("department_catalog_versions.catalog_version"), primary_key=True
    )
    department_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    work_assignments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    routing_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


class CatalogImportEvent(Base):
    """Append-only, complaint-free audit record for a catalog import."""

    __tablename__ = "catalog_import_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    catalog_version: Mapped[str] = mapped_column(
        ForeignKey("department_catalog_versions.catalog_version"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, default="catalog_imported")
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class Complaint(Base):
    __tablename__ = "complaints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    redacted_title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    redacted_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    location_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    redacted_location_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="web")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="received", index=True)

    category: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    subcategory: Mapped[str | None] = mapped_column(String(120), nullable=True)
    urgency: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    routing_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    classifier_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="none")
    classifier_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")

    assigned_department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True, index=True
    )
    candidate_departments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    missing_information: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    pii_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    emergency_signals: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    knowledge_source_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    answer_draft: Mapped[str] = mapped_column(Text, nullable=False, default="")

    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assigned_department: Mapped[Department | None] = relationship(lazy="joined")
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="complaint",
        cascade="all, delete-orphan",
        order_by="AuditEvent.id",
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[str] = mapped_column(
        ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    complaint: Mapped[Complaint] = relationship(back_populates="audit_events")


class ReviewDecision(Base):
    """Append-only snapshot of a human review action."""

    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[str] = mapped_column(
        ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    actor_role: Mapped[str] = mapped_column(String(40), nullable=False)
    department_id: Mapped[str] = mapped_column(
        ForeignKey("departments.id"), nullable=False, index=True
    )
    answer_draft: Mapped[str] = mapped_column(Text, nullable=False)
    draft_modified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    grounding_status: Mapped[str] = mapped_column(String(40), nullable=False)


class ComplaintLocationReview(Base):
    __tablename__ = "complaint_location_reviews"

    complaint_id: Mapped[str] = mapped_column(
        ForeignKey("complaints.id", ondelete="CASCADE"), primary_key=True
    )
    normalized_location_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    normalization_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unconfirmed", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DuplicateCandidate(Base):
    __tablename__ = "duplicate_candidates"
    __table_args__ = (
        UniqueConstraint(
            "complaint_id",
            "candidate_complaint_id",
            name="uq_duplicate_candidate_pair",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[str] = mapped_column(
        ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_complaint_id: Mapped[str] = mapped_column(
        ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    category_score: Mapped[float] = mapped_column(Float, nullable=False)
    location_score: Mapped[float] = mapped_column(Float, nullable=False)
    time_score: Mapped[float] = mapped_column(Float, nullable=False)
    text_score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    scoring_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="suggested", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GroundedDraftRecord(Base):
    """Additive citation snapshot for one complaint draft.

    Keeping this in a separate table allows existing prototype SQLite files to be opened without an
    in-place column migration. Reprocessing replaces the current machine-generated draft snapshot;
    the corresponding audit events retain the history of each decision.
    """

    __tablename__ = "grounded_draft_records"

    complaint_id: Mapped[str] = mapped_column(
        ForeignKey("complaints.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    sentences: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    rejected_sentences: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    retrieved_documents: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    retrieval_exclusions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
