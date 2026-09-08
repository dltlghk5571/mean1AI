"""Synthetic LoRA training-data generation for the ChatAgent's narrow contract:

    redacted transcript -> exact _ChatExtraction JSON (assistant_message,
    title, content, location_text, ready_to_submit)

Nothing else is ever a training target here. The output schema literally has
no department/routing/legal/eligibility/deadline field to fill in, and any
generated example whose target trips the app's own `evaluate_policy` or
`detect_emergency` signals is routed to `excluded_safety_signal` instead of
`sft_candidate` -- those behaviors stay governed by the reviewed rule-based
safety layer, never distilled into LoRA weights from a teacher-model guess.
Excluded records are never silently dropped: each one is written out (with
`safety_signal` recording which trigger(s) and detector output caused the
exclusion) in `excluded_safety_signal.jsonl`, separate from the SFT file, and
counted in the run summary. Only `evals/chat_training_select.py` decides what
is actually LoRA-ready, and it only ever accepts `bucket="sft_candidate"`
records that a human has already flipped to `review_status="approved"`.

This module is pure (no network, no filesystem, no DB) so it's cheap to unit
test with a fake `teacher_call`. The real OpenAI-backed teacher and file I/O
live in evals/chat_training_gen_run.py -- same split as
evals/chat_baseline.py / evals/chat_baseline_run.py.

Ownership note: this pipeline only ever produces `origin="synthetic_teacher"`
records in `review_status="draft"`. It never reads, generates, or writes
anything under eval_dataset/ -- that dataset is independently authored and
reviewed by a teammate per docs/CHAT_EVAL_DATA_GUIDELINE.md. The only channel
between the two sides is the reserved-seed registry (IDs and hashes only,
see evals/reserved_seed_registry.py) -- a reserved seed_id is skipped here
*before* the teacher is ever called, so eval scenarios are never exposed to
the training teacher model.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import Urgency
from app.services.chat_agent import _INSTRUCTIONS
from app.services.emergency import detect_emergency
from app.services.pii import redact_pii
from app.services.policy import evaluate_policy
from evals.chat_dataset import ChatModelOutput
from evals.text_similarity import content_hash, is_near_duplicate

GENERATION_VERSION = "chat-training-pilot.2026-09-08.v1"

SafetyTrigger = Literal[
    "case_type_emergency",
    "case_type_policy_sensitive",
    "policy_review_required",
    "emergency_detected",
]

RegionScope = Literal["national", "seongnam"]
CaseType = Literal[
    "normal", "incomplete", "ambiguous", "emergency", "policy_sensitive", "pii_bearing"
]
Bucket = Literal["sft_candidate", "excluded_safety_signal"]
RejectionReason = Literal[
    "seed_reserved_for_eval",
    "generation_failed",
    "pii_leak_in_target",
    "duplicate_content_hash",
    "near_duplicate_content",
]


class SeedTemplate(BaseModel):
    """A scenario brief authored by the training-data owner -- topic/region/
    case-type guidance for the teacher, never a full conversation. Public
    civil-complaint taxonomy (category, region) can ground this; the actual
    dialogue text is always teacher-generated from here."""

    model_config = ConfigDict(extra="forbid")

    seed_template_id: str = Field(min_length=1, max_length=100)
    region_scope: RegionScope
    category: str = Field(min_length=1, max_length=80)
    case_type: CaseType
    scenario_brief: str = Field(min_length=1, max_length=1000)
    source_id: str | None = None
    source_url: str | None = None
    license: str = "internal-synthetic-seed"


class TeacherGeneration(BaseModel):
    """The teacher model's structured output for one seed: a realistic
    (already-redacted-of-real-PII) transcript plus the exact _ChatExtraction
    target it should map to."""

    model_config = ConfigDict(extra="forbid")

    transcript: str = Field(min_length=1, max_length=4000)
    target: ChatModelOutput


TeacherCall = Callable[[SeedTemplate], TeacherGeneration]


class TrainingProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str | None
    seed_template_id: str
    region_scope: RegionScope
    origin: Literal["synthetic_teacher"]
    license: str
    teacher_model: str
    prompt_version: str
    review_status: Literal["draft", "approved", "rejected"] = "draft"
    content_hash: str
    generation_version: str


class SafetySignalDetail(BaseModel):
    """Why a record was routed to `excluded_safety_signal` instead of
    `sft_candidate`, plus the raw rule-based detector output that decided it
    -- so exclusion is visible and auditable, never a silent drop. Present
    only on excluded_safety_signal records; always None on sft_candidate
    ones (which never had a safety trigger to record)."""

    model_config = ConfigDict(extra="forbid")

    triggers: list[SafetyTrigger]
    policy_reasons: list[str]
    emergency_signals: list[str]
    emergency_urgency: str


class TrainingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    training_id: str
    case_type: CaseType
    bucket: Bucket
    messages: list[dict[str, str]]
    target: ChatModelOutput
    provenance: TrainingProvenance
    safety_signal: SafetySignalDetail | None = None


class RejectedGeneration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_template_id: str
    reason: RejectionReason
    detail: str


class GenerationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_version: str
    prompt_version: str
    teacher_model: str
    seeds_seen: int
    rejected_counts: dict[RejectionReason, int]
    bucket_counts: dict[Bucket, int]
    region_counts: dict[RegionScope, int]
    case_type_counts: dict[CaseType, int]
    excluded_safety_signal_trigger_counts: dict[str, int]


class TrainingGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[TrainingRecord]
    rejected: list[RejectedGeneration]
    summary: GenerationSummary


def _classify_safety(
    seed: SeedTemplate, target: ChatModelOutput
) -> tuple[Bucket, SafetySignalDetail | None]:
    text = f"{target.title}\n{target.content}\n{target.location_text}"
    policy = evaluate_policy(text, seed.category)
    emergency = detect_emergency(text)

    triggers: list[SafetyTrigger] = []
    if seed.case_type == "emergency":
        triggers.append("case_type_emergency")
    if seed.case_type == "policy_sensitive":
        triggers.append("case_type_policy_sensitive")
    if policy.requires_human_review:
        triggers.append("policy_review_required")
    if emergency.urgency != Urgency.NORMAL:
        triggers.append("emergency_detected")

    if not triggers:
        return "sft_candidate", None

    detail = SafetySignalDetail(
        triggers=triggers,
        policy_reasons=policy.reasons,
        emergency_signals=emergency.signals,
        emergency_urgency=emergency.urgency.value,
    )
    return "excluded_safety_signal", detail


def load_training_jsonl(paths: Sequence[Path]) -> list[TrainingRecord]:
    """Shared loader for anything that reads already-generated training
    output (the overlap checker, the trainable-output selector). Raises on
    malformed JSON/schema rather than skipping -- a bad line here means the
    generation or a manual edit broke the file, not a soft warning."""
    records: list[TrainingRecord] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(TrainingRecord.model_validate_json(line))
    return records


def generate_pilot(
    seeds: Sequence[SeedTemplate],
    teacher_call: TeacherCall,
    *,
    reserved_seed_ids: set[str],
    teacher_model: str,
    generation_version: str = GENERATION_VERSION,
    prompt_version: str = GENERATION_VERSION,
) -> TrainingGenerationResult:
    records: list[TrainingRecord] = []
    rejected: list[RejectedGeneration] = []
    seen_hash_by_seed: dict[str, str] = {}
    seen_transcripts: list[tuple[str, str]] = []

    for seed in seeds:
        if seed.seed_template_id in reserved_seed_ids:
            rejected.append(
                RejectedGeneration(
                    seed_template_id=seed.seed_template_id,
                    reason="seed_reserved_for_eval",
                    detail="reserved for the evaluation dataset; teacher was not called",
                )
            )
            continue

        try:
            generation = teacher_call(seed)
        except Exception as exc:  # noqa: BLE001 -- any teacher/schema failure is a rejection, not a crash
            rejected.append(
                RejectedGeneration(
                    seed_template_id=seed.seed_template_id,
                    reason="generation_failed",
                    detail=str(exc)[:200],
                )
            )
            continue

        target_text = (
            f"{generation.target.title}\n{generation.target.content}\n"
            f"{generation.target.location_text}"
        )
        redaction = redact_pii(target_text)
        if redaction.detected_types:
            rejected.append(
                RejectedGeneration(
                    seed_template_id=seed.seed_template_id,
                    reason="pii_leak_in_target",
                    detail=f"detected_types={redaction.detected_types}",
                )
            )
            continue

        digest = content_hash(generation.transcript)
        if digest in seen_hash_by_seed:
            rejected.append(
                RejectedGeneration(
                    seed_template_id=seed.seed_template_id,
                    reason="duplicate_content_hash",
                    detail=f"exact duplicate of seed {seen_hash_by_seed[digest]}",
                )
            )
            continue
        near_dup_seed = next(
            (
                other_seed_id
                for other_seed_id, other_text in seen_transcripts
                if is_near_duplicate(other_text, generation.transcript)
            ),
            None,
        )
        if near_dup_seed is not None:
            rejected.append(
                RejectedGeneration(
                    seed_template_id=seed.seed_template_id,
                    reason="near_duplicate_content",
                    detail=f"near-duplicate of seed {near_dup_seed}",
                )
            )
            continue

        seen_hash_by_seed[digest] = seed.seed_template_id
        seen_transcripts.append((seed.seed_template_id, generation.transcript))

        bucket, safety_signal = _classify_safety(seed, generation.target)
        records.append(
            TrainingRecord(
                training_id=seed.seed_template_id,
                case_type=seed.case_type,
                bucket=bucket,
                messages=[
                    {"role": "system", "content": _INSTRUCTIONS},
                    {"role": "user", "content": generation.transcript},
                ],
                target=generation.target,
                provenance=TrainingProvenance(
                    source_id=seed.source_id,
                    seed_template_id=seed.seed_template_id,
                    region_scope=seed.region_scope,
                    origin="synthetic_teacher",
                    license=seed.license,
                    teacher_model=teacher_model,
                    prompt_version=prompt_version,
                    content_hash=digest,
                    generation_version=generation_version,
                ),
                safety_signal=safety_signal,
            )
        )

    summary = _summarize(
        seeds, records, rejected, teacher_model, generation_version, prompt_version
    )
    return TrainingGenerationResult(records=records, rejected=rejected, summary=summary)


def _summarize(
    seeds: Sequence[SeedTemplate],
    records: Sequence[TrainingRecord],
    rejected: Sequence[RejectedGeneration],
    teacher_model: str,
    generation_version: str,
    prompt_version: str,
) -> GenerationSummary:
    rejected_counts: dict[RejectionReason, int] = defaultdict(int)
    for item in rejected:
        rejected_counts[item.reason] += 1
    bucket_counts: dict[Bucket, int] = defaultdict(int)
    region_counts: dict[RegionScope, int] = defaultdict(int)
    case_type_counts: dict[CaseType, int] = defaultdict(int)
    trigger_counts: dict[str, int] = defaultdict(int)
    for record in records:
        bucket_counts[record.bucket] += 1
        region_counts[record.provenance.region_scope] += 1
        case_type_counts[record.case_type] += 1
        if record.safety_signal is not None:
            for trigger in record.safety_signal.triggers:
                trigger_counts[trigger] += 1
    return GenerationSummary(
        generation_version=generation_version,
        prompt_version=prompt_version,
        teacher_model=teacher_model,
        seeds_seen=len(seeds),
        rejected_counts=dict(rejected_counts),
        bucket_counts=dict(bucket_counts),
        region_counts=dict(region_counts),
        case_type_counts=dict(case_type_counts),
        excluded_safety_signal_trigger_counts=dict(trigger_counts),
    )
