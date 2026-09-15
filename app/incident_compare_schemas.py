"""Versioned contract for a classifier LLM to compare two supplied, redacted candidate records."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ComparisonModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, str_strip_whitespace=True)


class Relation(StrEnum):
    SAME = "same_incident"
    DISTINCT = "related_distinct"
    RECURRENCE = "recurrence"
    UNCERTAIN = "uncertain"


class ComparisonInput(ComparisonModel):
    ref: Literal["current", "candidate"]
    title: str = Field(max_length=200)
    content: str = Field(max_length=4000)
    location: str = Field(max_length=300)
    content_truncated: bool
    category: str = Field(max_length=80)
    submitted_at: datetime
    urgency: Literal["normal", "high", "critical"]
    field_status: Literal["none", "checking", "in_progress", "resolved"]


class ComparisonRequest(ComparisonModel):
    schema_version: Literal["1"] = "1"
    task: Literal["compare_incidents"] = "compare_incidents"
    request_id: UUID
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1, max_length=120)
    current: ComparisonInput
    candidate: ComparisonInput


class ComparisonEvidence(ComparisonModel):
    ref: Literal["current", "candidate"]
    field: Literal["title", "content", "location"]
    quote: str = Field(min_length=4, max_length=180)


ShortComparisonText = Annotated[str, Field(min_length=2, max_length=180)]


class ComparisonProposal(ComparisonModel):
    relation: Relation
    summary: str = Field(min_length=5, max_length=400)
    evidence: list[ComparisonEvidence] = Field(max_length=6)
    differences: list[ShortComparisonText] = Field(max_length=5)
    questions: list[ShortComparisonText] = Field(max_length=3)


class ComparisonResponse(ComparisonModel):
    schema_version: Literal["1"]
    request_id: UUID
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1, max_length=120)
    proposal: ComparisonProposal
