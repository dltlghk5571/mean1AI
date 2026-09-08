"""CLI: import manually-pasted teacher responses for the offline manual-
teacher workflow (see evals/chat_training_prompt_export.py for the matching
prompt-export side).

You paste the prompt text from prompts/<seed_id>.txt into a subscription
ChatGPT session, copy the *raw* JSON response back, and save it as
manual_responses/<seed_id>.json (exactly one file per seed, containing both
requested variants -- see ManualResponseEnvelope below for the exact shape).
This command then applies the same acceptance rules as the API-backed
workflow (evals.chat_training_gen.evaluate_teacher_generation: PII leak
check, exact/near-duplicate dedup against everything already accepted in
this run, safety-signal bucketing) to each variant, so a manually-pasted
response is held to the same bar as an API response. Nothing here ever
repairs a malformed response -- an invalid file is rejected, not patched --
and nothing is ever auto-approved: every accepted record is written with
review_status="draft", exactly like the API workflow.

This command is designed to be re-run as you paste in more responses: it
always recomputes its full output from whatever is currently on disk under
manual_responses/ (no incremental state, no append-only file), so re-running
it after adding one more response file regenerates a complete, deduplicated
normalized/ output with no duplicate records -- and re-running it with
nothing changed reproduces byte-identical output.

Completeness (a response file that never arrived, or never parsed into a
usable variant) is tracked separately from content-quality rejection (a
variant that *was* present and failed the PII/dedup/safety-quality bar). A
missing manual_responses/<seed_id>.json is expected mid-pilot -- it is not a
rejected model generation, so it is never written to
rejected/content_rejected.jsonl or counted in content_rejected_count. It
shows up only in the completeness counts below and in
rejected/completeness_issues.jsonl.

Usage:
    python -m evals.chat_training_manual_import \\
        --run-dir dataset/pilot/<run_id> \\
        --seed-file evals/fixtures/chat_training_seed_templates.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evals.chat_dataset import ChatModelOutput
from evals.chat_training_gen import (
    Bucket,
    RejectedGeneration,
    SeedTemplate,
    TeacherGeneration,
    TrainingRecord,
    evaluate_teacher_generation,
    load_seed_templates_jsonl,
)

VARIANTS_PER_SEED = 2
VALID_VARIANT_IDS = frozenset({1, 2})


class ManualVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: int
    transcript: str = Field(min_length=1, max_length=4000)
    target: ChatModelOutput


class ManualResponseEnvelope(BaseModel):
    """The exact shape a human pastes into manual_responses/<seed_id>.json."""

    model_config = ConfigDict(extra="forbid")

    seed_template_id: str
    teacher_model: str = Field(min_length=1, max_length=200)
    variants: list[ManualVariant]


ReviewVerdict = Literal["pending", "pass", "fail"]


class ReviewEntry(BaseModel):
    """One row of the human-review sheet. Every judgment field starts
    "pending" -- this command never approves, passes, or fails anything on
    a reviewer's behalf."""

    model_config = ConfigDict(extra="forbid")

    training_id: str
    seed_template_id: str
    layer: str
    region_scope: str | None
    case_type: str | None
    transcript: str | None
    target: ChatModelOutput | None
    bucket: Bucket | Literal["rejected"]
    exclusion_reason: str
    schema_validity: Literal["valid", "invalid"]
    factual_faithfulness: ReviewVerdict = "pending"
    korean_naturalness: ReviewVerdict = "pending"
    one_question_at_a_time: ReviewVerdict = "pending"
    ready_to_submit_correctness: ReviewVerdict = "pending"
    hallucinated_detail_check: ReviewVerdict = "pending"
    pii_handling: ReviewVerdict = "pending"
    reviewer_decision: Literal["pending", "approve", "revise", "reject"] = "pending"
    reviewer_notes: str = ""


class ManualImportSummary(BaseModel):
    """Completeness (did the file/variant show up at all) and content-quality
    (did a present variant pass the same bar as the API workflow) are always
    reported as separate counts -- a missing response file is pilot progress,
    not a rejected generation, so it never inflates content_rejected_count.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str

    # Completeness: response-file level (one file expected per seed).
    expected_response_file_count: int
    present_response_file_count: int
    missing_response_file_count: int
    invalid_response_file_count: int

    # Completeness: variant level (two variants expected per seed).
    expected_variant_count: int
    present_variant_count: int
    missing_variant_count: int

    # Content quality: only ever counts a variant that was actually present
    # and failed evaluate_teacher_generation's PII/dedup gate.
    content_rejected_count: int

    # Outcome buckets for variants that passed the content-quality gate.
    accepted_draft_count: int
    excluded_safety_signal_count: int

    # Detail lists for drill-down, kept from the original implementation.
    expected_variant_keys: list[str]
    present_variant_keys: list[str]
    missing_variant_keys: list[str]
    duplicated_variant_keys: list[str]
    invalid_variant_keys: list[str]
    bucket_counts: dict[str, int]
    case_type_counts: dict[str, int]
    excluded_safety_signal_trigger_counts: dict[str, int]


def _layer(seed_template_id: str) -> str:
    # Pilot seed IDs are conventionally "l1-..."/"l2-..."; fall back to
    # "unknown" for any other naming scheme rather than guessing.
    prefix = seed_template_id.split("-", 1)[0].upper()
    return prefix if prefix in {"L1", "L2"} else "unknown"


def _variant_key(seed_id: str, variant_id: int) -> str:
    return f"{seed_id}::v{variant_id}"


def import_run(
    run_dir: Path, seeds_by_id: dict[str, SeedTemplate]
) -> tuple[
    list[TrainingRecord],
    list[RejectedGeneration],
    list[RejectedGeneration],
    list[ReviewEntry],
    ManualImportSummary,
]:
    """Pure-ish core (only reads manual_responses/*.json and the manifest;
    never writes) so the accept/reject decision logic is unit-testable
    without going through the CLI's file-writing side. Always recomputes
    from whatever is currently on disk -- no state carries over between
    calls -- so calling this again after adding more response files (or with
    nothing changed at all) is always safe and reproducible.

    Returns (records, content_rejected, completeness_issues, review, summary):
    - content_rejected: only ever populated by evaluate_teacher_generation,
      i.e. only for a variant that *was* present and failed the PII/dedup/
      safety-quality bar. This is the "rejected model generation" list.
    - completeness_issues: everything that happened *before* a variant ever
      reached evaluate_teacher_generation -- a missing/invalid response
      file, an unknown seed_template_id, a missing/duplicate/unexpected
      variant_id. Never a judgment on generated content, so never counted as
      a content rejection.
    """
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    expected_seed_ids: list[str] = manifest["expected_seed_ids"]
    expected_variant_keys: list[str] = manifest["expected_variant_keys"]
    prompt_version: str = manifest["prompt_version"]
    generation_version: str = manifest["generation_version"]

    records: list[TrainingRecord] = []
    content_rejected: list[RejectedGeneration] = []
    completeness_issues: list[RejectedGeneration] = []
    review: list[ReviewEntry] = []
    present: list[str] = []
    duplicated: list[str] = []
    invalid: list[str] = []

    present_response_file_ids: set[str] = set()
    missing_response_file_ids: set[str] = set()
    invalid_response_file_ids: set[str] = set()

    seen_hash_by_seed: dict[str, str] = {}
    seen_transcripts: list[tuple[str, str]] = []

    responses_dir = run_dir / "manual_responses"

    for seed_id in expected_seed_ids:
        response_path = responses_dir / f"{seed_id}.json"

        if not response_path.exists():
            missing_response_file_ids.add(seed_id)
            completeness_issues.append(
                RejectedGeneration(
                    seed_template_id=seed_id,
                    reason="response_file_missing",
                    detail=f"expected {response_path}",
                )
            )
            continue
        present_response_file_ids.add(seed_id)

        seed = seeds_by_id.get(seed_id)
        if seed is None:
            invalid_response_file_ids.add(seed_id)
            completeness_issues.append(
                RejectedGeneration(
                    seed_template_id=seed_id,
                    reason="seed_not_found_in_seed_file",
                    detail="seed_template_id not found in provided --seed-file",
                )
            )
            continue

        raw = response_path.read_text(encoding="utf-8")
        if "```" in raw:
            invalid_response_file_ids.add(seed_id)
            completeness_issues.append(
                RejectedGeneration(
                    seed_template_id=seed_id,
                    reason="markdown_fence_detected",
                    detail="response file contains a Markdown code fence ('```'); "
                    "paste raw JSON only, nothing else",
                )
            )
            continue

        try:
            parsed_json = json.loads(raw)
        except ValueError as exc:
            invalid_response_file_ids.add(seed_id)
            completeness_issues.append(
                RejectedGeneration(
                    seed_template_id=seed_id, reason="invalid_json", detail=str(exc)[:200]
                )
            )
            continue

        try:
            envelope = ManualResponseEnvelope.model_validate(parsed_json)
        except ValidationError as exc:
            invalid_response_file_ids.add(seed_id)
            completeness_issues.append(
                RejectedGeneration(
                    seed_template_id=seed_id, reason="schema_invalid", detail=str(exc)[:300]
                )
            )
            continue

        if envelope.seed_template_id != seed_id:
            invalid_response_file_ids.add(seed_id)
            completeness_issues.append(
                RejectedGeneration(
                    seed_template_id=seed_id,
                    reason="seed_id_mismatch",
                    detail=f"envelope declares seed_template_id={envelope.seed_template_id!r}, "
                    f"file is {response_path.name}",
                )
            )
            continue

        id_counts: dict[int, int] = defaultdict(int)
        for variant in envelope.variants:
            id_counts[variant.variant_id] += 1
        dup_ids = {vid for vid, count in id_counts.items() if count > 1}
        unexpected_ids = {vid for vid in id_counts if vid not in VALID_VARIANT_IDS}
        missing_ids = VALID_VARIANT_IDS - set(id_counts)

        if dup_ids:
            duplicated.extend(_variant_key(seed_id, vid) for vid in sorted(dup_ids))
            completeness_issues.append(
                RejectedGeneration(
                    seed_template_id=seed_id,
                    reason="duplicate_variant",
                    detail=f"variant_id(s) {sorted(dup_ids)} appear more than once",
                )
            )
        if unexpected_ids:
            invalid.extend(f"{seed_id}::v{vid}" for vid in sorted(unexpected_ids))
            completeness_issues.append(
                RejectedGeneration(
                    seed_template_id=seed_id,
                    reason="unexpected_variant",
                    detail=f"unexpected variant_id(s): {sorted(unexpected_ids)}",
                )
            )
        if missing_ids:
            completeness_issues.append(
                RejectedGeneration(
                    seed_template_id=seed_id,
                    reason="missing_variant",
                    detail=f"missing variant_id(s): {sorted(missing_ids)}",
                )
            )

        for variant in envelope.variants:
            if variant.variant_id not in VALID_VARIANT_IDS or id_counts[variant.variant_id] > 1:
                continue  # already reported above; never process an unexpected/duplicate copy
            key = _variant_key(seed_id, variant.variant_id)
            present.append(key)
            generation = TeacherGeneration(transcript=variant.transcript, target=variant.target)
            outcome = evaluate_teacher_generation(
                seed,
                generation,
                teacher_model=envelope.teacher_model,
                generation_version=generation_version,
                prompt_version=prompt_version,
                seen_hash_by_seed=seen_hash_by_seed,
                seen_transcripts=seen_transcripts,
                training_id=key,
            )
            if isinstance(outcome, RejectedGeneration):
                content_rejected.append(outcome)
                review.append(
                    ReviewEntry(
                        training_id=key,
                        seed_template_id=seed_id,
                        layer=_layer(seed_id),
                        region_scope=seed.region_scope,
                        case_type=seed.case_type,
                        transcript=variant.transcript,
                        target=variant.target,
                        bucket="rejected",
                        exclusion_reason=f"{outcome.reason}: {outcome.detail}",
                        schema_validity="valid",
                    )
                )
            else:
                records.append(outcome)
                review.append(
                    ReviewEntry(
                        training_id=key,
                        seed_template_id=seed_id,
                        layer=_layer(seed_id),
                        region_scope=seed.region_scope,
                        case_type=seed.case_type,
                        transcript=variant.transcript,
                        target=variant.target,
                        bucket=outcome.bucket,
                        exclusion_reason=(
                            ", ".join(outcome.safety_signal.triggers)
                            if outcome.safety_signal is not None
                            else ""
                        ),
                        schema_validity="valid",
                    )
                )

    # A variant key is "missing" iff it never made it into `present` --
    # covers a missing/invalid response file (both keys), an explicitly
    # absent variant_id, and a duplicate variant_id (ambiguous, so never
    # safely processed) uniformly, via one set difference instead of
    # tracking "missing" incrementally across five different branches above.
    missing_variant_keys = [key for key in expected_variant_keys if key not in set(present)]

    bucket_counts: dict[str, int] = defaultdict(int)
    case_type_counts: dict[str, int] = defaultdict(int)
    trigger_counts: dict[str, int] = defaultdict(int)
    for record in records:
        bucket_counts[record.bucket] += 1
        case_type_counts[record.case_type] += 1
        if record.safety_signal is not None:
            for trigger in record.safety_signal.triggers:
                trigger_counts[trigger] += 1

    summary = ManualImportSummary(
        run_id=manifest["run_id"],
        expected_response_file_count=len(expected_seed_ids),
        present_response_file_count=len(present_response_file_ids),
        missing_response_file_count=len(missing_response_file_ids),
        invalid_response_file_count=len(invalid_response_file_ids),
        expected_variant_count=len(expected_variant_keys),
        present_variant_count=len(present),
        missing_variant_count=len(missing_variant_keys),
        content_rejected_count=len(content_rejected),
        accepted_draft_count=bucket_counts.get("sft_candidate", 0),
        excluded_safety_signal_count=bucket_counts.get("excluded_safety_signal", 0),
        expected_variant_keys=expected_variant_keys,
        present_variant_keys=present,
        missing_variant_keys=missing_variant_keys,
        duplicated_variant_keys=duplicated,
        invalid_variant_keys=invalid,
        bucket_counts=dict(bucket_counts),
        case_type_counts=dict(case_type_counts),
        excluded_safety_signal_trigger_counts=dict(trigger_counts),
    )
    return records, content_rejected, completeness_issues, review, summary


def _write_jsonl(path: Path, items: Sequence[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n")


def write_import_result(
    run_dir: Path,
    records: Sequence[TrainingRecord],
    content_rejected: Sequence[RejectedGeneration],
    completeness_issues: Sequence[RejectedGeneration],
    review: Sequence[ReviewEntry],
    summary: ManualImportSummary,
) -> None:
    """Every file here is fully rewritten from the current in-memory result
    (never appended to), so calling this again after import_run recomputes
    from an updated manual_responses/ directory always produces a complete,
    non-duplicated normalized/ output -- and calling it again with nothing
    changed reproduces byte-identical files."""
    grouped: dict[str, list[TrainingRecord]] = defaultdict(list)
    for record in records:
        grouped[f"{record.provenance.region_scope}/{record.bucket}"].append(record)
    for key, group in grouped.items():
        _write_jsonl(run_dir / "normalized" / f"{key}.jsonl", group)
    _write_jsonl(run_dir / "rejected" / "content_rejected.jsonl", content_rejected)
    _write_jsonl(run_dir / "rejected" / "completeness_issues.jsonl", completeness_issues)
    _write_jsonl(run_dir / "review" / "review.jsonl", review)
    (run_dir / "summary.json").write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import manually-pasted teacher responses from manual_responses/ "
        "into normalized draft training output, applying the same PII/dedup/safety "
        "checks as the API-backed workflow."
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--seed-file", type=Path, action="append", default=[], required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.run_dir / "manifest.json").exists():
        print(
            f"No manifest.json found under {args.run_dir}; run the exporter first.", file=sys.stderr
        )
        return 2
    try:
        seeds = load_seed_templates_jsonl(args.seed_file)
    except (OSError, ValueError) as exc:
        print(f"Unable to load seed templates: {exc}", file=sys.stderr)
        return 2

    seeds_by_id = {seed.seed_template_id: seed for seed in seeds}
    records, content_rejected, completeness_issues, review, summary = import_run(
        args.run_dir, seeds_by_id
    )
    write_import_result(
        args.run_dir, records, content_rejected, completeness_issues, review, summary
    )

    print(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
