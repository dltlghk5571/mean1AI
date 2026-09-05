from datetime import date, datetime
from enum import StrEnum
from typing import Literal

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
    catalog_version: str | None = Field(default=None, max_length=80)
    work_assignment_ids: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("work_assignment_ids")
    @classmethod
    def work_assignment_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("work_assignment_ids must be unique")
        return value


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=80)
    subcategory: str = Field(min_length=1, max_length=120)
    urgency: Urgency = Urgency.NORMAL
    candidates: list[ClassificationCandidate] = Field(min_length=1, max_length=3)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    requires_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    evidence_summary: str = Field(max_length=500)
    provider: str = Field(min_length=1, max_length=40)

    @field_validator("candidates")
    @classmethod
    def sort_candidates(
        cls, candidates: list[ClassificationCandidate]
    ) -> list[ClassificationCandidate]:
        return sorted(candidates, key=lambda item: item.confidence, reverse=True)


class GroundedDraftSentence(BaseModel):
    """One provider-produced sentence and its explicit knowledge support."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2_000)
    substantive: bool
    source_ids: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("text")
    @classmethod
    def strip_sentence(cls, value: str) -> str:
        return value.strip()

    @field_validator("source_ids")
    @classmethod
    def source_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source_ids must be unique")
        return value


class StructuredDraftOutput(BaseModel):
    """Provider boundary: free-form draft output is not accepted."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=40)
    sentences: list[GroundedDraftSentence] = Field(min_length=1, max_length=30)


class RejectedDraftSentence(GroundedDraftSentence):
    reason: str = Field(min_length=1, max_length=160)


class KnowledgeSourceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    category: str
    version: str
    effective_from: date
    effective_until: date | None
    approval_status: str
    retrieval_score: float = Field(ge=0.0, le=1.0)


class RetrievalExclusionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    reason: str


class GroundedDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    complaint_id: str
    provider: str
    validation_status: str
    sentences: list[GroundedDraftSentence]
    rejected_sentences: list[RejectedDraftSentence]
    retrieved_documents: list[KnowledgeSourceRead]
    retrieval_exclusions: list[RetrievalExclusionRead]
    created_at: datetime
    updated_at: datetime


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
    actor_role: str = Field(min_length=1, max_length=40)


class ComplaintApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: str = Field(min_length=1, max_length=64)
    answer_draft: str = Field(min_length=1, max_length=20_000)


class LocationReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    normalized_location_text: str | None
    normalization_version: str
    status: str
    confirmed_by: str | None
    confirmed_at: datetime | None


class DuplicateDecision(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class DuplicateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DuplicateDecision


class SessionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    display_name: str
    role: str
    role_label: str
    permissions: list[str]
    csrf_token: str
    expires_at: int


class DuplicateScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: float = Field(ge=0.0, le=1.0)
    location: float = Field(ge=0.0, le=1.0)
    time: float = Field(ge=0.0, le=1.0)
    text: float = Field(ge=0.0, le=1.0)


class DuplicateCandidateRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_complaint_id: str
    redacted_title: str
    redacted_location_text: str | None
    category: str | None
    created_at: datetime
    total_score: float = Field(ge=0.0, le=1.0)
    score_breakdown: DuplicateScoreBreakdown
    evidence: list[str]
    review_status: str
    reviewed_by: str | None
    reviewed_at: datetime | None


class DepartmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    category: str
    description: str
    jurisdiction: str
    active: bool


class WorkAssignmentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str


class RoutingRuleRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    subcategory: str
    keywords: list[str]
    requires_location: bool
    work_assignment_ids: list[str]


class DepartmentCatalogDepartmentRead(DepartmentRead):
    work_assignments: list[WorkAssignmentRead]
    routing_rules: list[RoutingRuleRead]


class DepartmentCatalogRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_version: str
    supersedes: str | None = None
    effective_from: date
    effective_until: date | None
    approval_status: str
    source_label: str
    synthetic: bool
    source_sha256: str
    fallback_department_id: str
    departments: list[DepartmentCatalogDepartmentRead]


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    action: str
    actor_type: str
    actor_id: str | None
    details: dict[str, object]


class ReviewDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    complaint_id: str
    created_at: datetime
    actor_id: str
    actor_role: str
    department_id: str
    answer_draft: str
    draft_modified: bool
    grounding_status: str


class AIProcessingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    state: Literal["queued", "processing", "completed", "failed"]
    attempts: int
    max_attempts: int
    available_at: datetime
    lease_expires_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


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
    ai_processing: AIProcessingRead | None = None


class ComplaintDetail(ComplaintRead):
    audit_events: list[AuditEventRead]
