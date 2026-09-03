import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from evals.evaluator import evaluate
from evals.loader import DEFAULT_FIXTURES_DIR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline civic complaint evaluation suite."
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES_DIR,
        help="Directory containing routing, urgency, pii, and abstention JSONL files.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate(args.fixtures)
    except (OSError, ValueError) as exc:
        print(f"Evaluation input error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
