from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CatalogImportEvent,
    Complaint,
    Department,
    DepartmentCatalogEntry,
    DepartmentCatalogVersion,
)
from app.schemas import ComplaintStatus
from app.services.audit import record_audit
from app.services.classifier import DepartmentCatalog, DepartmentInfo


def current_catalog_event(db: Session) -> CatalogImportEvent | None:
    """Use the append-only import sequence, never an opaque version label or wall clock."""
    return db.scalar(select(CatalogImportEvent).order_by(CatalogImportEvent.id.desc()).limit(1))


def ensure_current_catalog(db: Session, catalog: DepartmentCatalog) -> None:
    catalog.ensure_effective()
    event = current_catalog_event(db)
    if event is None:
        raise ValueError("catalog_not_imported")
    if (
        event.catalog_version != catalog.catalog_version
        or event.source_sha256 != catalog.source_sha256
    ):
        raise ValueError("catalog_superseded")


def _department_snapshot(department: DepartmentInfo) -> dict[str, object]:
    return department.model_dump(mode="json")


def _entry_snapshot(entry: DepartmentCatalogEntry) -> dict[str, object]:
    return {
        "id": entry.department_id,
        "name": entry.name,
        "category": entry.category,
        "description": entry.description,
        "jurisdiction": entry.jurisdiction,
        "active": entry.active,
        "work_assignments": entry.work_assignments,
        "routing_rules": entry.routing_rules,
    }


def _load_version_entries(
    db: Session,
    catalog_version: str,
) -> dict[str, DepartmentCatalogEntry]:
    entries = db.scalars(
        select(DepartmentCatalogEntry).where(
            DepartmentCatalogEntry.catalog_version == catalog_version
        )
    ).all()
    return {entry.department_id: entry for entry in entries}


def _sync_current_projection(
    db: Session,
    catalog: DepartmentCatalog,
) -> None:
    current_ids = {department.id for department in catalog.all_departments}
    for department in catalog.all_departments:
        projection = db.get(Department, department.id)
        if projection is None:
            projection = Department(id=department.id)
            db.add(projection)
        projection.name = department.name
        projection.category = department.category
        projection.description = department.description
        projection.jurisdiction = department.jurisdiction
        projection.active = department.active

    # Also retire unmanaged legacy rows on the first import, preserving their foreign keys.
    for projection in db.scalars(select(Department).where(Department.id.not_in(current_ids))):
        projection.active = False


def _validate_stable_ids(
    db: Session, catalog: DepartmentCatalog, previous: dict[str, DepartmentCatalogEntry]
) -> None:
    categories: dict[str, set[str]] = {}
    assignment_owners: dict[str, set[str]] = {}
    rule_owners: dict[str, set[str]] = {}
    for entry in db.scalars(select(DepartmentCatalogEntry)):
        categories.setdefault(entry.department_id, set()).add(entry.category)
        for historical_assignment in entry.work_assignments:
            assignment_owners.setdefault(historical_assignment["id"], set()).add(
                entry.department_id
            )
        for historical_rule in entry.routing_rules:
            rule_owners.setdefault(historical_rule["id"], set()).add(entry.department_id)

    previous_assignments = {
        assignment["id"]
        for entry in previous.values()
        if entry.active
        for assignment in entry.work_assignments
    }
    previous_rules = {
        rule["id"] for entry in previous.values() if entry.active for rule in entry.routing_rules
    }
    for department in catalog.all_departments:
        if department.id in categories and categories[department.id] != {department.category}:
            raise ValueError("stable department ID cannot change category")
        if department.active and department.id in categories:
            prior = previous.get(department.id)
            if prior is None or not prior.active:
                raise ValueError("retired department ID cannot be reactivated")
        for assignment in department.work_assignments:
            owner = assignment_owners.get(assignment.id)
            if owner is not None and owner != {department.id}:
                raise ValueError("stable work-assignment ID cannot change department")
            if owner and department.active and assignment.id not in previous_assignments:
                raise ValueError("retired work-assignment ID cannot be reactivated")
        for rule in department.routing_rules:
            owner = rule_owners.get(rule.id)
            if owner is not None and owner != {department.id}:
                raise ValueError("stable routing-rule ID cannot change department")
            if owner and rule.id not in previous_rules:
                raise ValueError("retired routing-rule ID cannot be reactivated")


def _invalidate_automatic_routes(db: Session, catalog: DepartmentCatalog) -> int:
    complaints = db.scalars(
        select(Complaint).where(Complaint.status == ComplaintStatus.ASSIGNED)
    ).all()
    for complaint in complaints:
        previous_department_id = complaint.assigned_department_id
        complaint.assigned_department_id = None
        complaint.requires_human_review = True
        complaint.status = ComplaintStatus.NEEDS_REVIEW.value
        record_audit(
            db,
            complaint_id=complaint.id,
            action="catalog_route_invalidated",
            actor_type="system",
            details={
                "reason": "catalog_changed_or_legacy_provenance_missing",
                "previous_department_id": previous_department_id,
                "catalog_version": catalog.catalog_version,
                "source_sha256": catalog.source_sha256,
                "external_system_connected": False,
            },
        )
    return len(complaints)


def import_department_catalog(db: Session, catalog: DepartmentCatalog) -> bool:
    """Import one immutable catalog version and refresh the current department projection.

    Returns ``True`` only when a new immutable version was inserted. Re-reading the exact same
    current version is idempotent. Rejected imports roll back the entire transaction. This service
    owns the transaction; call with a dedicated session, as startup does.
    """
    try:
        inserted = _import_department_catalog(db, catalog)
        db.commit()
        return inserted
    except Exception:
        db.rollback()
        raise


def _import_department_catalog(db: Session, catalog: DepartmentCatalog) -> bool:
    catalog.ensure_effective()
    current_event = current_catalog_event(db)
    previous_version = (
        db.get(DepartmentCatalogVersion, current_event.catalog_version) if current_event else None
    )
    if previous_version is None and db.scalar(select(DepartmentCatalogVersion).limit(1)):
        raise ValueError("catalog import history is incomplete")

    incoming = {
        department.id: _department_snapshot(department) for department in catalog.all_departments
    }
    existing_version = db.get(DepartmentCatalogVersion, catalog.catalog_version)
    if existing_version is not None:
        if current_event is None or current_event.catalog_version != catalog.catalog_version:
            raise ValueError("superseded catalog version cannot be imported")
        if existing_version.source_sha256 != catalog.source_sha256:
            raise ValueError(
                f"Catalog version {catalog.catalog_version} was reused with different content"
            )
        stored_entries = _load_version_entries(db, catalog.catalog_version)
        stored = {key: _entry_snapshot(value) for key, value in stored_entries.items()}
        if stored != incoming:
            raise ValueError(
                f"Stored catalog snapshot {catalog.catalog_version} does not match its source"
            )
        metadata_fields = (
            "effective_from",
            "effective_until",
            "approval_status",
            "source_label",
            "synthetic",
            "fallback_department_id",
        )
        if any(getattr(existing_version, key) != getattr(catalog, key) for key in metadata_fields):
            raise ValueError("stored catalog metadata does not match its source")
        expected_counts = {
            "department_count": len(catalog.all_departments),
            "work_assignment_count": sum(
                len(row.work_assignments) for row in catalog.all_departments
            ),
            "routing_rule_count": len(catalog.routing_rules),
        }
        if (
            any(getattr(existing_version, key) != count for key, count in expected_counts.items())
            or current_event.source_sha256 != catalog.source_sha256
        ):
            raise ValueError("stored catalog counts or import checksum do not match its source")
        if catalog.supersedes is not None and catalog.supersedes != current_event.details.get(
            "previous_catalog_version"
        ):
            raise ValueError("stored catalog predecessor does not match its source")
        _sync_current_projection(db, catalog)
        return False

    if previous_version:
        if catalog.supersedes != previous_version.catalog_version:
            raise ValueError("new catalog must explicitly supersede the current catalog version")
        if catalog.effective_from < previous_version.effective_from:
            raise ValueError("successor effective_from cannot move backwards")
    elif catalog.supersedes is not None:
        raise ValueError("cannot import a catalog whose predecessor is missing")
    previous_entries = (
        _load_version_entries(db, previous_version.catalog_version) if previous_version else {}
    )
    _validate_stable_ids(db, catalog, previous_entries)
    previous = {key: _entry_snapshot(value) for key, value in previous_entries.items()}

    current_ids = set(incoming)
    previous_ids = set(previous)
    added_ids = sorted(current_ids - previous_ids)
    removed_ids = sorted(previous_ids - current_ids)
    changed_ids = sorted(
        department_id
        for department_id in current_ids & previous_ids
        if incoming[department_id] != previous[department_id]
    )
    deactivated_ids = sorted(
        set(removed_ids)
        | {
            department_id
            for department_id in current_ids & previous_ids
            if bool(previous[department_id]["active"])
            and not bool(incoming[department_id]["active"])
        }
    )

    version = DepartmentCatalogVersion(
        catalog_version=catalog.catalog_version,
        effective_from=catalog.effective_from,
        effective_until=catalog.effective_until,
        approval_status=catalog.approval_status,
        source_label=catalog.source_label,
        synthetic=catalog.synthetic,
        source_sha256=catalog.source_sha256,
        fallback_department_id=catalog.fallback_department_id,
        department_count=len(catalog.all_departments),
        work_assignment_count=sum(
            len(department.work_assignments) for department in catalog.all_departments
        ),
        routing_rule_count=len(catalog.routing_rules),
    )
    db.add(version)
    db.flush()
    for department in catalog.all_departments:
        db.add(
            DepartmentCatalogEntry(
                catalog_version=catalog.catalog_version,
                department_id=department.id,
                name=department.name,
                category=department.category,
                description=department.description,
                jurisdiction=department.jurisdiction,
                active=department.active,
                work_assignments=[
                    assignment.model_dump(mode="json") for assignment in department.work_assignments
                ],
                routing_rules=[rule.model_dump(mode="json") for rule in department.routing_rules],
            )
        )
    invalidated_count = _invalidate_automatic_routes(db, catalog)
    db.add(
        CatalogImportEvent(
            catalog_version=catalog.catalog_version,
            source_sha256=catalog.source_sha256,
            details={
                "previous_catalog_version": (
                    previous_version.catalog_version if previous_version else None
                ),
                "added_department_ids": added_ids,
                "changed_department_ids": changed_ids,
                "deactivated_department_ids": deactivated_ids,
                "department_count": len(catalog.all_departments),
                "work_assignment_count": version.work_assignment_count,
                "routing_rule_count": version.routing_rule_count,
                "invalidated_automatic_route_count": invalidated_count,
                "synthetic": True,
                "external_system_connected": False,
            },
        )
    )
    _sync_current_projection(db, catalog)
    return True
