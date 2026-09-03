from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Channel(StrEnum):
    WEB = "web"
    SMS = "sms"
    CALL_CENTER = "call_center"
    NATIONAL_PORTAL = "national_portal"
    OTHER = "other"


class Urgency(StrEnum):
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class ComplaintStatus(StrEnum):
    RECEIVED = "received"
    NEEDS_REVIEW = "needs_review"
    URGENT_REVIEW = "urgent_review"
    ASSIGNED = "assigned"
    REVIEWED = "reviewed"


class ClassificationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=300)


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=80)
    subcategory: str = Field(min_length=1, max_length=120)
    urgency: Urgency = Urgency.NORMAL
    candidates: list[ClassificationCandidate] = Field(min_length=1, max_length=3)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    requires_human_review: bool = False
    evidence_summary: str = Field(max_length=500)
    provider: str = Field(min_length=1, max_length=40)

    @field_validator("candidates")
    @classmethod
    def sort_candidates(
        cls, candidates: list[ClassificationCandidate]
    ) -> list[ClassificationCandidate]:
        return sorted(candidates, key=lambda item: item.confidence, reverse=True)


class ComplaintCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=5, max_length=20_000)
    location_text: str | None = Field(default=None, max_length=300)
    channel: Channel = Channel.WEB

    @field_validator("title", "content")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must contain non-whitespace text")
        return stripped

    @field_validator("location_text")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ComplaintApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: str = Field(min_length=1, max_length=64)
    answer_draft: str = Field(min_length=1, max_length=20_000)
    actor_id: str = Field(min_length=1, max_length=120)


class DepartmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    category: str
    description: str
    jurisdiction: str
    active: bool


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    action: str
    actor_type: str
    actor_id: str | None
    details: dict[str, object]


class ComplaintRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime
    redacted_title: str
    redacted_content: str
    redacted_location_text: str | None
    channel: str
    status: str
    category: str | None
    subcategory: str | None
    urgency: str
    routing_confidence: float
    requires_human_review: bool
    classifier_provider: str
    classifier_evidence: str
    assigned_department_id: str | None
    candidate_departments: list[dict[str, object]]
    missing_information: list[str]
    pii_types: list[str]
    emergency_signals: list[str]
    knowledge_source_ids: list[str]
    answer_draft: str
    reviewed_by: str | None
    reviewed_at: datetime | None


class ComplaintDetail(ComplaintRead):
    audit_events: list[AuditEventRead]
