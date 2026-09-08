"""Machine-readable schema for the Seongnam chat-drafting gold evaluation set.

Ownership: this dataset is authored and reviewed independently by a teammate,
following docs/CHAT_EVAL_DATA_GUIDELINE.md. Nothing in this repo generates or
populates eval_dataset/ records -- this module only defines the schema (so
both sides agree on the exact shape) and a loader used by
evals/chat_eval_validate.py.

`expected` reuses evals.chat_dataset.ChatModelOutput -- the eval target is the
exact same _ChatExtraction shape the training data targets, so a model can be
scored against either with one code path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evals.chat_dataset import ChatModelOutput

EVAL_SCHEMA_VERSION = "chat-eval-record.2026-09-08.v1"

District = Literal["bundang", "sujeong", "jungwon", "unknown"]

CaseType = Literal[
    "complete",
    "incomplete_one_followup",
    "vague_location",
    "unknown_time",
    "ambiguous_request",
    "multi_issue",
    "emergency",
    "policy_sensitive",
    "pii_bearing_fictional",
    "prompt_injection",
    "not_ready_to_submit",
    "real_landmark_unverified_jurisdiction",
]

ReviewStatus = Literal["draft", "approved", "rejected"]


class EvalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_id: str = Field(min_length=1, max_length=100)
    seed_id: str = Field(min_length=1, max_length=100)
    source_id: str | None = None
    source_url: str | None = None
    region: Literal["seongnam"]
    district: District
    case_type: CaseType
    transcript: str = Field(min_length=1, max_length=4000)
    expected: ChatModelOutput
    required_facts: list[str] = Field(default_factory=list, max_length=20)
    forbidden_inferences: list[str] = Field(default_factory=list, max_length=20)
    safety_expectation: str = Field(default="", max_length=500)
    reviewer: str = Field(min_length=1, max_length=120)
    review_status: ReviewStatus
    review_notes: str = Field(default="", max_length=1000)
    content_hash: str = Field(min_length=64, max_length=64)
    dataset_version: str = Field(min_length=1, max_length=40)


def load_eval_jsonl(path: Path) -> list[EvalRecord]:
    """Same duplicate-id-checking convention as evals/loader.py's
    load_jsonl, keyed on `eval_id` instead of a generic `id` field."""
    if not path.exists():
        return []
    records: list[EvalRecord] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = EvalRecord.model_validate_json(line)
        except ValidationError as exc:
            raise ValueError(f"Invalid eval record at {path}:{line_number}: {exc}") from exc
        if record.eval_id in seen_ids:
            raise ValueError(f"Duplicate eval_id at {path}:{line_number}: {record.eval_id}")
        seen_ids.add(record.eval_id)
        records.append(record)
    return records
