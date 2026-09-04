from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import Channel, Urgency

DATASET_VERSION = "2026-09-03.v1"
PiiType = Literal[
    "resident_registration_number",
    "email",
    "mobile_phone",
    "landline_phone",
]


class SyntheticCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: Literal["2026-09-03.v1"]
    id: str = Field(min_length=3, max_length=100, pattern=r"^[a-z0-9-]+$")
    synthetic: Literal[True]


class RoutingCase(SyntheticCase):
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=5, max_length=2_000)
    location_text: str | None = Field(default=None, max_length=300)
    expected_category: str = Field(min_length=1, max_length=80)
    expected_department_id: str = Field(min_length=1, max_length=64)


class UrgencyCase(SyntheticCase):
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=5, max_length=2_000)
    location_text: str | None = Field(default=None, max_length=300)
    expected_urgency: Urgency
    expected_signals: list[str] = Field(default_factory=list, max_length=10)


class PiiTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=3, max_length=200)
    pii_type: PiiType


class PiiCase(SyntheticCase):
    text: str = Field(min_length=3, max_length=2_000)
    targets: list[PiiTarget] = Field(min_length=1, max_length=10)


class AbstentionCase(SyntheticCase):
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=5, max_length=2_000)
    location_text: str | None = Field(default=None, max_length=300)
    channel: Channel = Channel.WEB
    reason: str = Field(min_length=3, max_length=120)
    sensitive: bool = False


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routing: list[RoutingCase]
    urgency: list[UrgencyCase]
    pii: list[PiiCase]
    abstention: list[AbstentionCase]

    @property
    def total_cases(self) -> int:
        return len(self.routing) + len(self.urgency) + len(self.pii) + len(self.abstention)


class RatioMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float = Field(ge=0.0, le=1.0)

    @classmethod
    def from_counts(cls, numerator: int, denominator: int) -> Self:
        value = numerator / denominator if denominator else 1.0
        return cls(numerator=numerator, denominator=denominator, value=value)


class RoutingCategoryMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: int = Field(ge=0)
    top1_accuracy: RatioMetric
    top3_accuracy: RatioMetric


class RoutingCategoryThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_cases: int = Field(ge=1)
    minimum_top1_accuracy: float = Field(ge=0.0, le=1.0)
    minimum_top3_accuracy: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def top3_threshold_cannot_be_lower_than_top1(self) -> Self:
        if self.minimum_top3_accuracy < self.minimum_top1_accuracy:
            raise ValueError("minimum_top3_accuracy must be at least minimum_top1_accuracy")
        return self


class RoutingThresholdConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thresholds_version: str = Field(min_length=3, max_length=80)
    dataset_version: Literal["2026-09-03.v1"]
    categories: dict[str, RoutingCategoryThreshold]


class RoutingConfusionMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: list[str]
    rows: dict[str, dict[str, int]]
    total_cases: int = Field(ge=0)


class EvaluationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routing_top1_accuracy: RatioMetric
    routing_top3_accuracy: RatioMetric
    routing_by_category: dict[str, RoutingCategoryMetrics]
    routing_confusion_matrix: RoutingConfusionMatrix
    emergency_recall: RatioMetric
    emergency_false_positive_rate: RatioMetric
    pii_masking_recall: RatioMetric
    pii_masking_recall_by_type: dict[str, RatioMetric]
    abstention_rate: RatioMetric
    sensitive_auto_assigned_count: int = Field(ge=0)
    sensitive_auto_finalized_count: int = Field(ge=0)
    urgent_auto_assigned_count: int = Field(ge=0)
    urgent_not_reviewed_count: int = Field(ge=0)
    unaudited_processing_count: int = Field(ge=0)


class CaseFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite: Literal["routing", "urgency", "pii", "abstention"]
    case_id: str
    check: str
    expected: str
    actual: str


class GateFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate: str
    expected: str
    actual: str


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    thresholds_version: str
    provider: Literal["rules"]
    case_counts: dict[str, int]
    total_cases: int = Field(ge=0)
    metrics: EvaluationMetrics
    routing_thresholds: RoutingThresholdConfig
    failures: list[CaseFailure]
    gate_failures: list[GateFailure]
    passed: bool
