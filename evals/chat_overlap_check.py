"""Cross-check training data against the (independently authored) evaluation
dataset and the shared reserved-seed registry, before any real training run.

This never deletes or edits anything on either side -- it only reports
suspected overlap for a human to look at, per the leakage-prevention rules in
docs/CHAT_EVAL_DATA_GUIDELINE.md#6. Exit code is 1 if anything is found, so it
can gate a training run in a script/CI step without a human having to read
the report every time nothing is wrong.

Usage:
    python -m evals.chat_overlap_check \
        --training-file dataset/chat_training_pilot_v1/seongnam/sft_candidate.jsonl \
        --eval-file eval_dataset/seongnam_gold.jsonl \
        --registry eval_dataset/reserved_seed_registry.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from evals.chat_eval_schema import EvalRecord
from evals.chat_training_gen import TrainingRecord, load_training_jsonl
from evals.reserved_seed_registry import ReservedSeedEntry, load_registry
from evals.text_similarity import is_near_duplicate

DEFAULT_REGISTRY = Path("eval_dataset/reserved_seed_registry.jsonl")

OverlapReason = Literal["seed_id_overlap", "exact_hash_overlap", "near_duplicate_overlap"]


class OverlapFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    training_id: str
    reason: OverlapReason
    detail: str


class OverlapReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    training_records_checked: int
    eval_records_checked: int
    registry_entries_checked: int
    findings: list[OverlapFinding]


def check_overlap(
    training_records: Sequence[TrainingRecord],
    eval_records: Sequence[EvalRecord],
    registry_entries: Sequence[ReservedSeedEntry],
) -> OverlapReport:
    eval_seed_ids = {record.seed_id for record in eval_records} | {
        entry.seed_id for entry in registry_entries
    }
    eval_hashes = {record.content_hash for record in eval_records} | {
        entry.content_hash for entry in registry_entries if entry.content_hash
    }
    eval_transcripts = [record.transcript for record in eval_records]

    findings: list[OverlapFinding] = []
    for record in training_records:
        seed_id = record.provenance.seed_template_id
        if seed_id in eval_seed_ids:
            findings.append(
                OverlapFinding(
                    training_id=record.training_id,
                    reason="seed_id_overlap",
                    detail=f"seed_template_id {seed_id} is reserved/used on the eval side",
                )
            )
            continue
        if record.provenance.content_hash in eval_hashes:
            findings.append(
                OverlapFinding(
                    training_id=record.training_id,
                    reason="exact_hash_overlap",
                    detail="content_hash matches an eval record or registry entry",
                )
            )
            continue
        transcript = record.messages[-1]["content"] if record.messages else ""
        for eval_transcript in eval_transcripts:
            if is_near_duplicate(transcript, eval_transcript):
                findings.append(
                    OverlapFinding(
                        training_id=record.training_id,
                        reason="near_duplicate_overlap",
                        detail="transcript is a near-duplicate of an eval transcript",
                    )
                )
                break

    return OverlapReport(
        training_records_checked=len(training_records),
        eval_records_checked=len(eval_records),
        registry_entries_checked=len(registry_entries),
        findings=findings,
    )


def _load_eval_records(paths: Sequence[Path]) -> list[EvalRecord]:
    records: list[EvalRecord] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(EvalRecord.model_validate_json(line))
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report suspected overlap between training data and eval data."
    )
    parser.add_argument("--training-file", type=Path, action="append", required=True)
    parser.add_argument("--eval-file", type=Path, action="append", default=[])
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        training_records = load_training_jsonl(args.training_file)
        eval_records = _load_eval_records(args.eval_file)
        registry_entries = load_registry(args.registry)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Unable to load input files: {exc}", file=sys.stderr)
        return 2

    report = check_overlap(training_records, eval_records, registry_entries)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report.findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
