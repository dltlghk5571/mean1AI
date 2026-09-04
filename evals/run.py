import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from evals.evaluator import evaluate
from evals.loader import DEFAULT_FIXTURES_DIR, DEFAULT_THRESHOLDS_PATH
from evals.models import EvaluationReport


def _percent(value: float) -> str:
    return f"{value:.1%}"


def format_markdown(report: EvaluationReport) -> str:
    result_label = "PASS" if report.passed else "FAIL"
    lines = [
        "# Offline evaluation report",
        "",
        f"- Result: **{result_label}**",
        f"- Dataset: `{report.dataset_version}` ({report.total_cases} cases)",
        f"- Thresholds: `{report.thresholds_version}`",
        f"- Provider: `{report.provider}`",
        "",
        "## Routing category gates",
        "",
        "| Category | Cases | Top-1 | Minimum | Top-3 | Minimum | Result |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for category, threshold in sorted(report.routing_thresholds.categories.items()):
        category_metrics = report.metrics.routing_by_category[category]
        passed = (
            category_metrics.cases >= threshold.minimum_cases
            and category_metrics.top1_accuracy.value >= threshold.minimum_top1_accuracy
            and category_metrics.top3_accuracy.value >= threshold.minimum_top3_accuracy
        )
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{category}`",
                    str(category_metrics.cases),
                    _percent(category_metrics.top1_accuracy.value),
                    _percent(threshold.minimum_top1_accuracy),
                    _percent(category_metrics.top3_accuracy.value),
                    _percent(threshold.minimum_top3_accuracy),
                    "PASS" if passed else "FAIL",
                )
            )
            + " |"
        )

    matrix = report.metrics.routing_confusion_matrix
    lines.extend(
        [
            "",
            "## Routing confusion matrix",
            "",
            "Rows are expected categories; columns are predicted categories.",
            "",
            "| Expected \\ Predicted | "
            + " | ".join(f"`{label}`" for label in matrix.labels)
            + " | Total |",
            "|---|" + "---:|" * (len(matrix.labels) + 1),
        ]
    )
    for expected in matrix.labels:
        row = matrix.rows[expected]
        values = [row[predicted] for predicted in matrix.labels]
        lines.append(
            f"| `{expected}` | "
            + " | ".join(str(value) for value in values)
            + f" | {sum(values)} |"
        )

    overall_metrics = report.metrics
    lines.extend(
        [
            "",
            "## Safety metrics",
            "",
            "| Metric | Result |",
            "|---|---:|",
            f"| Emergency recall | {_percent(overall_metrics.emergency_recall.value)} |",
            "| Emergency false-positive rate | "
            f"{_percent(overall_metrics.emergency_false_positive_rate.value)} |",
            f"| PII masking recall | {_percent(overall_metrics.pii_masking_recall.value)} |",
            f"| Human-review abstention rate | {_percent(overall_metrics.abstention_rate.value)} |",
            f"| Gate failures | {len(report.gate_failures)} |",
            f"| Case failures | {len(report.failures)} |",
        ]
    )
    return "\n".join(lines) + "\n"


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
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=DEFAULT_THRESHOLDS_PATH,
        help="Versioned JSON file containing per-category routing thresholds.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        dest="output_format",
        help="Report output format. JSON remains the CI default.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate(args.fixtures, args.thresholds)
    except (OSError, ValueError) as exc:
        print(f"Evaluation input error: {exc}", file=sys.stderr)
        return 2

    if args.output_format == "markdown":
        print(format_markdown(report), end="")
    else:
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
