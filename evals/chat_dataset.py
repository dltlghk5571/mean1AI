"""Offline export of the LangGraph chat-drafting agent's per-turn eval logs into
normalized, leakage-safe JSONL splits for a future LoRA fine-tuning run.

Two independent, already-collected inputs are joined by draft_id:
  - `app.chat_eval` log lines: one JSON object per chat turn that reached the
    safety gate (see app/services/chat_agent.py::_safety_gate). These are
    never written to the DB -- the app's "no server-side conversation
    storage" design means this ordinary log stream is the only place a
    transcript + model draft ever exists.
  - `citizen_chat_draft` AuditEvent rows (app/services/citizen.py::submit),
    which record only draft_id + a self-reported `edited` flag, never the
    draft text itself.

Everything in this module is pure (no DB, no network, no filesystem) so it is
cheap to unit test; DB/file I/O lives in evals/chat_dataset_run.py.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.chat_agent import _INSTRUCTIONS

DATASET_SCHEMA_VERSION = "chat-draft.2026-09-07.v1"
# ponytail: fixed 80/10/10 split via seeded hash buckets, promote to a config
# field if a different ratio is ever needed.
_TRAIN_CUTOFF = 8_000
_VAL_CUTOFF = 9_000
_BUCKET_SPACE = 10_000

SplitCategory = Literal["accepted_unedited", "accepted_edited", "unsubmitted", "safety_eval"]
SplitPart = Literal["train", "val", "test"]
RejectionReason = Literal[
    "malformed_json",
    "schema_invalid",
    "duplicate_draft_id_log",
    "duplicate_draft_id_audit",
    "missing_eval_log_for_audit",
    "audit_row_invalid",
]

_LOG_MARKER = "chat_turn_extraction "


class ChatModelOutput(BaseModel):
    """Exactly the `_ChatExtraction` field set (see app/services/chat_agent.py)."""

    model_config = ConfigDict(extra="forbid")

    assistant_message: str
    title: str
    content: str
    location_text: str
    ready_to_submit: bool


class ChatEvalLogRecord(BaseModel):
    """One parsed `app.chat_eval` log line. Already PII-redacted by the producer."""

    model_config = ConfigDict(extra="forbid")

    draft_id: str | None
    transcript: str
    model_output: ChatModelOutput
    safety_overridden: bool
    final_ready: bool


class AuditDraftRow(BaseModel):
    """One `citizen_chat_draft` AuditEvent row, as loaded from the DB."""

    model_config = ConfigDict(extra="forbid")

    complaint_id: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)
    edited: bool


class RejectedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: RejectionReason
    detail: str


class DatasetRecord(BaseModel):
    """One model-ready training/eval example."""

    model_config = ConfigDict(extra="forbid")

    draft_id: str | None
    complaint_id: str | None
    category: SplitCategory
    split: SplitPart | None
    edited: bool | None
    messages: list[dict[str, str]]
    target: ChatModelOutput


class FieldLengthStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0)
    min: int = Field(ge=0)
    max: int = Field(ge=0)
    mean: float = Field(ge=0.0)


class DatasetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    seed: int
    log_lines_seen: int
    log_lines_matched: int
    audit_rows_seen: int
    rejected_counts: dict[RejectionReason, int]
    category_counts: dict[SplitCategory, int]
    split_counts: dict[SplitCategory, dict[SplitPart, int]]
    field_length_by_category: dict[SplitCategory, dict[str, FieldLengthStats]]


class ChatDatasetResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[DatasetRecord]
    rejected: list[RejectedRecord]
    summary: DatasetSummary


def parse_eval_log_lines(
    lines: Sequence[str],
) -> tuple[list[ChatEvalLogRecord], list[RejectedRecord], int]:
    """Extract chat_eval JSON payloads from raw log lines.

    Lines without the marker are unrelated log output and are silently
    skipped (not counted as matched or rejected). The prefix before the
    marker (timestamp/level/logger name) is ignored, so this tolerates any
    log formatter or aggregator wrapping.
    """
    records: list[ChatEvalLogRecord] = []
    rejected: list[RejectedRecord] = []
    matched = 0
    for line in lines:
        index = line.find(_LOG_MARKER)
        if index == -1:
            continue
        matched += 1
        payload = line[index + len(_LOG_MARKER) :].strip()
        try:
            raw = json.loads(payload)
        except ValueError:
            rejected.append(RejectedRecord(reason="malformed_json", detail=payload[:120]))
            continue
        try:
            records.append(ChatEvalLogRecord.model_validate(raw))
        except ValidationError as exc:
            rejected.append(RejectedRecord(reason="schema_invalid", detail=str(exc)[:200]))
    return records, rejected, matched


def _dedup_log_records(
    records: Sequence[ChatEvalLogRecord],
) -> tuple[dict[str, ChatEvalLogRecord], list[ChatEvalLogRecord], list[RejectedRecord]]:
    """Split into (unique-by-draft_id, null-draft_id passthrough, duplicate rejections)."""
    by_id: dict[str, list[ChatEvalLogRecord]] = defaultdict(list)
    null_draft: list[ChatEvalLogRecord] = []
    for record in records:
        if record.draft_id is None:
            null_draft.append(record)
        else:
            by_id[record.draft_id].append(record)
    unique: dict[str, ChatEvalLogRecord] = {}
    rejected: list[RejectedRecord] = []
    for draft_id, group in by_id.items():
        if len(group) > 1:
            rejected.append(
                RejectedRecord(
                    reason="duplicate_draft_id_log",
                    detail=f"draft_id={draft_id} appears {len(group)} times in chat_eval logs",
                )
            )
        else:
            unique[draft_id] = group[0]
    return unique, null_draft, rejected


def _dedup_audit_rows(
    rows: Sequence[AuditDraftRow],
) -> tuple[dict[str, AuditDraftRow], list[RejectedRecord]]:
    by_id: dict[str, list[AuditDraftRow]] = defaultdict(list)
    for row in rows:
        by_id[row.draft_id].append(row)
    unique: dict[str, AuditDraftRow] = {}
    rejected: list[RejectedRecord] = []
    for draft_id, group in by_id.items():
        if len(group) > 1:
            rejected.append(
                RejectedRecord(
                    reason="duplicate_draft_id_audit",
                    detail=(
                        f"draft_id={draft_id} appears {len(group)} times in "
                        "citizen_chat_draft audit events"
                    ),
                )
            )
        else:
            unique[draft_id] = group[0]
    return unique, rejected


def _assign_split(group_key: str, seed: int) -> SplitPart:
    """Deterministic given (seed, group_key); order-independent so growing the
    dataset later never reshuffles already-assigned groups."""
    digest = hashlib.sha256(f"{seed}:{group_key}".encode()).hexdigest()
    bucket = int(digest[:8], 16) % _BUCKET_SPACE
    if bucket < _TRAIN_CUTOFF:
        return "train"
    if bucket < _VAL_CUTOFF:
        return "val"
    return "test"


def _to_dataset_record(
    log_record: ChatEvalLogRecord,
    *,
    category: SplitCategory,
    complaint_id: str | None,
    edited: bool | None,
    seed: int,
    group_key: str | None,
) -> DatasetRecord:
    return DatasetRecord(
        draft_id=log_record.draft_id,
        complaint_id=complaint_id,
        category=category,
        split=_assign_split(group_key, seed) if group_key is not None else None,
        edited=edited,
        messages=[
            {"role": "system", "content": _INSTRUCTIONS},
            {"role": "user", "content": log_record.transcript},
        ],
        target=log_record.model_output,
    )


def build_dataset(
    log_lines: Sequence[str],
    audit_rows: Sequence[AuditDraftRow],
    *,
    seed: int,
    extra_rejected: Sequence[RejectedRecord] = (),
) -> ChatDatasetResult:
    parsed, rejected, matched = parse_eval_log_lines(log_lines)
    rejected = [*rejected, *extra_rejected]

    unique_by_draft, null_draft_records, dup_log_rejects = _dedup_log_records(parsed)
    rejected += dup_log_rejects
    unique_audit, dup_audit_rejects = _dedup_audit_rows(audit_rows)
    rejected += dup_audit_rejects

    records: list[DatasetRecord] = []

    # safety_eval: draft_id is null and/or safety_overridden -- never a positive
    # SFT candidate. Both conditions are checked explicitly (today the mint
    # logic in _safety_gate makes them equivalent -- safety_overridden always
    # implies draft_id is None -- but that coupling is an implementation
    # detail this export must not silently rely on).
    for log_record in null_draft_records:
        records.append(
            _to_dataset_record(
                log_record,
                category="safety_eval",
                complaint_id=None,
                edited=None,
                seed=seed,
                group_key=None,
            )
        )

    for draft_id, log_record in unique_by_draft.items():
        if log_record.safety_overridden:
            records.append(
                _to_dataset_record(
                    log_record,
                    category="safety_eval",
                    complaint_id=None,
                    edited=None,
                    seed=seed,
                    group_key=None,
                )
            )
            continue
        audit = unique_audit.get(draft_id)
        if audit is None:
            records.append(
                _to_dataset_record(
                    log_record,
                    category="unsubmitted",
                    complaint_id=None,
                    edited=None,
                    seed=seed,
                    group_key=draft_id,
                )
            )
        else:
            category: SplitCategory = "accepted_edited" if audit.edited else "accepted_unedited"
            records.append(
                _to_dataset_record(
                    log_record,
                    category=category,
                    complaint_id=audit.complaint_id,
                    edited=audit.edited,
                    seed=seed,
                    group_key=audit.complaint_id,
                )
            )

    for draft_id, audit in unique_audit.items():
        if draft_id not in unique_by_draft:
            rejected.append(
                RejectedRecord(
                    reason="missing_eval_log_for_audit",
                    detail=(
                        f"draft_id={draft_id} complaint_id={audit.complaint_id} has no "
                        "matching chat_eval log entry"
                    ),
                )
            )

    summary = _summarize(
        records,
        rejected,
        log_lines_seen=len(log_lines),
        log_lines_matched=matched,
        audit_rows_seen=len(audit_rows),
        seed=seed,
    )
    return ChatDatasetResult(records=records, rejected=rejected, summary=summary)


def _summarize(
    records: Sequence[DatasetRecord],
    rejected: Sequence[RejectedRecord],
    *,
    log_lines_seen: int,
    log_lines_matched: int,
    audit_rows_seen: int,
    seed: int,
) -> DatasetSummary:
    rejected_counts: dict[RejectionReason, int] = defaultdict(int)
    for item in rejected:
        rejected_counts[item.reason] += 1

    category_counts: dict[SplitCategory, int] = defaultdict(int)
    split_counts: dict[SplitCategory, dict[SplitPart, int]] = defaultdict(lambda: defaultdict(int))
    lengths: dict[SplitCategory, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        category_counts[record.category] += 1
        if record.split is not None:
            split_counts[record.category][record.split] += 1
        lengths[record.category]["title"].append(len(record.target.title))
        lengths[record.category]["content"].append(len(record.target.content))
        lengths[record.category]["location_text"].append(len(record.target.location_text))
        lengths[record.category]["assistant_message"].append(len(record.target.assistant_message))

    field_length_by_category: dict[SplitCategory, dict[str, FieldLengthStats]] = {
        category: {
            field: FieldLengthStats(
                count=len(values), min=min(values), max=max(values), mean=statistics.mean(values)
            )
            for field, values in fields.items()
        }
        for category, fields in lengths.items()
    }

    return DatasetSummary(
        schema_version=DATASET_SCHEMA_VERSION,
        seed=seed,
        log_lines_seen=log_lines_seen,
        log_lines_matched=log_lines_matched,
        audit_rows_seen=audit_rows_seen,
        rejected_counts=dict(rejected_counts),
        category_counts=dict(category_counts),
        split_counts={k: dict(v) for k, v in split_counts.items()},
        field_length_by_category=field_length_by_category,
    )
