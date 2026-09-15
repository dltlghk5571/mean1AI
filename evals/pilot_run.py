"""python -m evals.pilot_run: offline handoff for the future classifier and dialogue agent."""

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from app.services.citizen_questions import templates
from evals.pilot_evaluator import (
    DEFAULT_PILOT_FIXTURE,
    SAFE_ACTIONS,
    export_rows,
    load_cases,
    registry,
    score,
    source_review,
    validate_dataset,
)
from evals.pilot_models import PILOT_VERSION, PilotInput, Prediction, Split


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Synthetic pilot data; no network/model calls")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("validate", "export", "score", "contract", "source-review"):
        command = commands.add_parser(name)
        command.add_argument(
            "--output", type=Path, help="New UTF-8 file; existing files are refused"
        )
        if name in {"validate", "export", "score"}:
            command.add_argument("--fixture", type=Path, default=DEFAULT_PILOT_FIXTURE)
        if name in {"export", "score"}:
            command.add_argument("--split", choices=["train", "dev", "test"], required=True)
        if name == "export":
            command.add_argument("--with-labels", action="store_true", help="Train seed only")
        if name == "score":
            command.add_argument("--predictions", type=Path, required=True)
            command.add_argument(
                "--model-run", required=True, help="Model/checkpoint/run identifier"
            )
    return root


def contract() -> dict[str, object]:
    fields = registry()
    return {
        "dataset_version": PILOT_VERSION,
        "purpose": "offline_evaluation_only",
        "catalog_status": "unapproved_candidates",
        "department_id_must_be_null": True,
        "safe_actions": sorted(SAFE_ACTIONS),
        "input_schema": PilotInput.model_json_schema(),
        "prediction_schema": Prediction.model_json_schema(),
        "services": [
            {
                "service_id": f"pilot-service-{key}",
                "template_id": key,
                "title": item.title,
                "allowed_fields": sorted(fields[f"pilot-service-{key}"]),
                "questions": [q.model_dump(mode="json") for q in item.questions],
            }
            for key, item in templates().items()
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    exit_code = 0
    try:
        if args.command == "export":
            rows = export_rows(load_cases(args.fixture), cast(Split, args.split), args.with_labels)
            output = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
        else:
            if args.command == "validate":
                data = validate_dataset(args.fixture)
            elif args.command == "score":
                if not args.model_run.strip() or len(args.model_run) > 120:
                    raise ValueError(
                        "model-run must be a nonblank identifier of at most 120 characters"
                    )
                report = score(
                    args.predictions, cast(Split, args.split), args.model_run, args.fixture
                )
                data = report.model_dump(mode="json")
                exit_code = 0 if report.all_cases_match else 1
            elif args.command == "contract":
                data = contract()
            else:
                data = source_review()
            output = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(output)
        else:
            sys.stdout.write(output)
    except (OSError, ValueError) as exc:
        print(f"Pilot evaluation input error: {exc}", file=sys.stderr)
        return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
