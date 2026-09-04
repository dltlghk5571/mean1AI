from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RAG_DATASET_VERSION = "2026-09-04.rag-v1"


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: Literal["2026-09-04.rag-v1"]
    id: str = Field(min_length=3, max_length=100, pattern=r"^[a-z0-9-]+$")
    synthetic: Literal[True]
    category: str = Field(min_length=1, max_length=80)
    query_text: str = Field(min_length=2, max_length=500)
    expected_source_ids: list[str] = Field(max_length=3)
    query_kind: Literal["direct", "paraphrase", "irrelevant"]


class RetrievalMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)
    direct_recall: float = Field(ge=0.0, le=1.0)
    paraphrase_recall: float = Field(ge=0.0, le=1.0)
    irrelevant_rejection_rate: float = Field(ge=0.0, le=1.0)
    abstention_rate: float = Field(ge=0.0, le=1.0)
    true_positive_count: int = Field(ge=0)
    false_positive_count: int = Field(ge=0)
    false_negative_count: int = Field(ge=0)


class RetrievalStrategyReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str
    metrics: RetrievalMetrics


class RagEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    case_count: int
    provider: Literal["rules"]
    baseline: RetrievalStrategyReport
    candidate: RetrievalStrategyReport
    safety_gate_failures: list[str]
    passed: bool
