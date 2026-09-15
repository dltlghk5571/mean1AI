"""Human-only shared incident commands; no model-generated or automatic approval inputs."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IncidentStatus(StrEnum):
    CHECKING = "checking"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class IncidentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    reason: str = Field(min_length=5, max_length=500)
    confirm: Literal["yes"]


class CreateIncident(IncidentInput):
    complaint_id: UUID
    candidate_id: UUID
    title: str = Field(min_length=2, max_length=120)


class IncidentRevision(IncidentInput):
    revision: int = Field(ge=1)


class LinkComplaint(IncidentRevision):
    complaint_id: UUID
    candidate_id: UUID


class UnlinkComplaint(IncidentRevision):
    complaint_id: UUID


class ChangeIncidentStatus(IncidentRevision):
    status: IncidentStatus


class PublishIncident(IncidentRevision):
    message: str = Field(min_length=5, max_length=2000)
