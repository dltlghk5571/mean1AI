"""Baseline evaluation of the current (pre-fine-tuning) chat-drafting model
against the held-out test split produced by evals/chat_dataset_run.py.

This does not fine-tune anything. It re-runs the *existing* prompt/model on
transcripts it already produced targets for, giving a self-consistency
baseline to compare a future LoRA model against. It is NOT an external
ground-truth quality score -- the only real signal about draft quality is
whether the citizen accepted it unedited (see the accepted_unedited /
accepted_edited split in evals/chat_dataset.py).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from evals.chat_dataset import ChatModelOutput, DatasetRecord


class BaselineCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str | None
    ready_match: bool
    title_token_overlap: float = Field(ge=0.0, le=1.0)
    content_token_overlap: float = Field(ge=0.0, le=1.0)
    location_token_overlap: float = Field(ge=0.0, le=1.0)


class BaselineReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_count: int = Field(ge=0)
    ready_agreement_rate: float = Field(ge=0.0, le=1.0)
    mean_title_token_overlap: float = Field(ge=0.0, le=1.0)
    mean_content_token_overlap: float = Field(ge=0.0, le=1.0)
    mean_location_token_overlap: float = Field(ge=0.0, le=1.0)
    cases: list[BaselineCaseResult]


def _token_overlap(a: str, b: str) -> float:
    """Jaccard overlap on whitespace tokens; a cheap, dependency-free proxy
    for text similarity. Good enough for a directional baseline, not a
    substitute for a real quality metric."""
    tokens_a, tokens_b = set(a.split()), set(b.split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def score_prediction(
    draft_id: str | None, target: ChatModelOutput, predicted: ChatModelOutput
) -> BaselineCaseResult:
    return BaselineCaseResult(
        draft_id=draft_id,
        ready_match=target.ready_to_submit == predicted.ready_to_submit,
        title_token_overlap=_token_overlap(target.title, predicted.title),
        content_token_overlap=_token_overlap(target.content, predicted.content),
        location_token_overlap=_token_overlap(target.location_text, predicted.location_text),
    )


def run_baseline(
    records: Sequence[DatasetRecord],
    predict: Callable[[DatasetRecord], ChatModelOutput],
) -> BaselineReport:
    cases = [
        score_prediction(record.draft_id, record.target, predict(record)) for record in records
    ]
    if not cases:
        return BaselineReport(
            case_count=0,
            ready_agreement_rate=1.0,
            mean_title_token_overlap=1.0,
            mean_content_token_overlap=1.0,
            mean_location_token_overlap=1.0,
            cases=[],
        )
    count = len(cases)
    return BaselineReport(
        case_count=count,
        ready_agreement_rate=sum(c.ready_match for c in cases) / count,
        mean_title_token_overlap=sum(c.title_token_overlap for c in cases) / count,
        mean_content_token_overlap=sum(c.content_token_overlap for c in cases) / count,
        mean_location_token_overlap=sum(c.location_token_overlap for c in cases) / count,
        cases=cases,
    )
