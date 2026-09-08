"""CLI: validate one or more eval_dataset/*.jsonl files against EvalRecord.

For the eval-data teammate to run locally before asking for review, and for
either side to run before evals/chat_overlap_check.py (which assumes its
input files already parse). This never edits or judges dataset *content*
(that's the human-review checklist in docs/CHAT_EVAL_DATA_GUIDELINE.md#5) --
it only checks that every line is well-formed and every eval_id is unique.

Usage:
    python -m evals.chat_eval_validate --file eval_dataset/seongnam_gold.jsonl
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from evals.chat_eval_schema import EvalRecord


def validate_file(path: Path) -> list[str]:
    """Returns a list of human-readable error strings; empty means clean."""
    errors: list[str] = []
    if not path.exists():
        return [f"{path}: file does not exist"]
    seen_ids: set[str] = set()
    lines = path.read_text(encoding="utf-8").splitlines()
    if not any(line.strip() for line in lines):
        return [f"{path}: file is empty (expected at least one eval record)"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = EvalRecord.model_validate_json(line)
        except ValidationError as exc:
            errors.append(f"{path}:{line_number}: {exc}")
            continue
        if record.eval_id in seen_ids:
            errors.append(f"{path}:{line_number}: duplicate eval_id {record.eval_id}")
        seen_ids.add(record.eval_id)
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate eval_dataset JSONL files.")
    parser.add_argument("--file", type=Path, action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    all_errors: list[str] = []
    for path in args.file:
        all_errors.extend(validate_file(path))
    if all_errors:
        for error in all_errors:
            print(error, file=sys.stderr)
        print(f"FAILED: {len(all_errors)} error(s)", file=sys.stderr)
        return 1
    print(f"OK: {len(args.file)} file(s) valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
