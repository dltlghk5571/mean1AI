"""Offline, synthetic evaluation contract; not a production agent API."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

PILOT_VERSION = "2026-09-09.pilot-v1"
Split = Literal["train", "dev", "test"]
Intent = Literal["complaint", "information", "mixed", "unclear", "out_of_scope"]
Action = Literal[
    "ask", "clarify", "retrieve_information", "review_draft", "urgent_guidance", "handoff"
]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$")]


class PilotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Message(PilotModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class PilotInput(PilotModel):
    messages: list[Message] = Field(min_length=1, max_length=12)
    known_fields: dict[Identifier, ShortText] = Field(default_factory=dict, max_length=12)
    skipped_fields: list[Identifier] = Field(default_factory=list, max_length=12)
    submission_confirmed: Literal[False] = False


class Gold(PilotModel):
    service_id: Identifier | None
    intent: Intent
    urgent: bool
    needs_human_review: Literal[True] = True
    # Each field contains acceptable verbatim spans, not inferred or normalized values.
    extracted_fields: dict[Identifier, list[ShortText]] = Field(default_factory=dict, max_length=12)
    next_actions: list[Action] = Field(min_length=1, max_length=4)
    question_fields: list[Identifier] = Field(default_factory=list, max_length=12)


class PilotCase(PilotModel):
    dataset_version: Literal["2026-09-09.pilot-v1"]
    id: str = Field(pattern=r"^pc[0-9]{3}$")
    family_id: str = Field(pattern=r"^pf[0-9]{3}$")
    split: Split
    synthetic: Literal[True]
    review_status: Literal["draft"]
    tags: list[Identifier] = Field(min_length=1, max_length=12)
    input: PilotInput
    expected: Gold
    rationale: str = Field(min_length=5, max_length=800)


class Prediction(PilotModel):
    dataset_version: Literal["2026-09-09.pilot-v1"]
    id: str = Field(pattern=r"^pc[0-9]{3}$")
    service_id: Identifier | None
    intent: Intent
    urgent: bool
    needs_human_review: bool
    department_id: Identifier | None
    extracted_fields: dict[Identifier, ShortText] = Field(max_length=12)
    # Unknown actions/fields/IDs are scored as failures, rather than silently corrected.
    next_action: Identifier
    questions: list[Identifier] = Field(max_length=12)


class Metric(PilotModel):
    numerator: int
    denominator: int
    value: float | None

    @classmethod
    def count(cls, numerator: int, denominator: int) -> "Metric":
        return cls(
            numerator=numerator,
            denominator=denominator,
            value=numerator / denominator if denominator else None,
        )


class Failure(PilotModel):
    id: str
    checks: list[str]


class PilotReport(PilotModel):
    dataset_version: str = PILOT_VERSION
    split: Split
    model_run: str
    dataset_sha256: str
    predictions_sha256: str
    review_status: Literal["draft"] = "draft"
    model_quality_certified: Literal[False] = False
    total_cases: int
    metrics: dict[str, Metric]
    service_macro_f1: float
    service_metrics: dict[str, dict[str, float | int | None]]
    safety_violations: dict[str, int]
    failures: list[Failure]
    all_cases_match: bool
