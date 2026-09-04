from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CatalogImportEvent,
    Department,
    DepartmentCatalogEntry,
    DepartmentCatalogVersion,
)
from app.services.classifier import DepartmentCatalog, DepartmentInfo


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
    previously_managed_ids: set[str],
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

    removed_ids = previously_managed_ids - current_ids
    for department_id in removed_ids:
        projection = db.get(Department, department_id)
        if projection is not None:
            projection.active = False


def import_department_catalog(db: Session, catalog: DepartmentCatalog) -> bool:
    """Import one immutable catalog version and refresh the current department projection.

    Returns ``True`` only when a new immutable version was inserted. Re-reading the exact same
    version is idempotent. Reusing a version string for different bytes fails closed.
    """

    incoming = {
        department.id: _department_snapshot(department) for department in catalog.all_departments
    }
    existing_version = db.get(DepartmentCatalogVersion, catalog.catalog_version)
    if existing_version is not None:
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
        _sync_current_projection(db, catalog, set(stored_entries))
        db.commit()
        return False

    previous_version = db.scalar(
        select(DepartmentCatalogVersion)
        .order_by(
            DepartmentCatalogVersion.imported_at.desc(),
            DepartmentCatalogVersion.catalog_version.desc(),
        )
        .limit(1)
    )
    previous_entries = (
        _load_version_entries(db, previous_version.catalog_version) if previous_version else {}
    )
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
                "synthetic": True,
                "external_system_connected": False,
            },
        )
    )
    _sync_current_projection(db, catalog, previous_ids)
    db.commit()
    return True


def seed_departments(db: Session, path: Path) -> None:
    import_department_catalog(db, DepartmentCatalog.from_json(path))
