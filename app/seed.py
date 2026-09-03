import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Department


def load_department_rows(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as file:
        rows = json.load(file)
    if not isinstance(rows, list):
        raise ValueError("departments.json must contain a list")
    return rows


def seed_departments(db: Session, path: Path) -> None:
    existing = set(db.scalars(select(Department.id)).all())
    changed = False
    for row in load_department_rows(path):
        department_id = str(row["id"])
        if department_id in existing:
            continue
        db.add(
            Department(
                id=department_id,
                name=str(row["name"]),
                category=str(row["category"]),
                description=str(row["description"]),
                jurisdiction=str(row.get("jurisdiction", "성남시 데모")),
                active=bool(row.get("active", True)),
            )
        )
        changed = True
    if changed:
        db.commit()
