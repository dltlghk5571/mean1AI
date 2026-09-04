from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Department
from app.schemas import DepartmentCatalogRead, DepartmentRead

router = APIRouter(prefix="/api/v1/departments", tags=["departments"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[DepartmentRead])
def list_departments(db: DbSession) -> list[Department]:
    return list(
        db.scalars(
            select(Department).where(Department.active.is_(True)).order_by(Department.name)
        ).all()
    )


@router.get("/catalog", response_model=DepartmentCatalogRead)
def get_department_catalog(request: Request) -> dict[str, object]:
    """Expose the active, synthetic catalog and its immutable source checksum."""

    return request.app.state.pipeline.catalog.as_api_data()
