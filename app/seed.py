"""Compatibility entry points for existing demo and evaluation scripts."""

from pathlib import Path

from sqlalchemy.orm import Session

from app.services.classifier import DepartmentCatalog
from app.services.department_catalog import import_department_catalog as import_department_catalog


def seed_departments(db: Session, path: Path) -> None:
    import_department_catalog(db, DepartmentCatalog.from_json(path))
