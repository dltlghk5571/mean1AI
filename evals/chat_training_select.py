"""CLI: select the LoRA-ready subset out of already-generated, already-reviewed
training output.

`evals/chat_training_gen_run.py` only ever writes `review_status="draft"`
records (see that module's docstring) -- a human reviews a generated file and
flips `provenance.review_status` to `"approved"` (or `"rejected"`) in place
once satisfied, exactly like the eval side's review workflow in
docs/CHAT_EVAL_DATA_GUIDELINE.md#5. This command is the only place that turns
reviewed files into a trainable file: it never mutates a record to make it
pass, it only filters. A record is included if and only if its bucket is
"sft_candidate" and its `provenance.review_status` is "approved" -- draft,
rejected, and excluded_safety_signal records are always left out, and the
counts of what was left out (and why) are printed so nothing is silently
dropped.

Usage:
    python -m evals.chat_training_select \
        --training-file dataset/chat_training_pilot_v1/seongnam/sft_candidate.jsonl \
        --out dataset/chat_training_pilot_v1/lora_ready.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from evals.chat_training_gen import TrainingRecord, load_training_jsonl


class SelectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_count: int
    selected_count: int
    skipped_by_reason: dict[str, int]


def select_trainable(
    records: Sequence[TrainingRecord],
) -> tuple[list[TrainingRecord], SelectionSummary]:
    """Pure filter: never edits a record's fields, only decides whether it
    passes through unchanged. Returns the selected records plus a summary of
    everything that was left out and why, so exclusion is always visible."""
    skipped_by_reason: dict[str, int] = defaultdict(int)
    selected: list[TrainingRecord] = []
    for record in records:
        if record.bucket != "sft_candidate":
            skipped_by_reason[f"bucket:{record.bucket}"] += 1
            continue
        if record.provenance.review_status != "approved":
            skipped_by_reason[f"review_status:{record.provenance.review_status}"] += 1
            continue
        selected.append(record)
    summary = SelectionSummary(
        input_count=len(records),
        selected_count=len(selected),
        skipped_by_reason=dict(skipped_by_reason),
    )
    return selected, summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select only bucket=sft_candidate, review_status=approved "
        "records from already-generated training output into a LoRA-ready file."
    )
    parser.add_argument("--training-file", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        records = load_training_jsonl(args.training_file)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Unable to load input files: {exc}", file=sys.stderr)
        return 2

    selected, summary = select_trainable(records)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for record in selected:
            handle.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")

    print(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
