import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from evals.rag_evaluator import DEFAULT_RAG_FIXTURE, evaluate_rag
from evals.rag_models import RagEvaluationReport


def _percent(value: float) -> str:
    return f"{value:.1%}"


def format_markdown(report: RagEvaluationReport) -> str:
    lines = [
        "# Offline grounded-retrieval evaluation",
        "",
        f"- Result: **{'PASS' if report.passed else 'FAIL'}**",
        f"- Dataset: `{report.dataset_version}` ({report.case_count} synthetic cases)",
        f"- Provider: `{report.provider}` (offline; no embeddings)",
        "",
        "| Strategy | Precision | Recall | F1 | Direct recall | Paraphrase recall | "
        "Irrelevant rejection | Abstention |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy in (report.baseline, report.candidate):
        metrics = strategy.metrics
        lines.append(
            f"| `{strategy.strategy}` | {_percent(metrics.precision)} | "
            f"{_percent(metrics.recall)} | {_percent(metrics.f1)} | "
            f"{_percent(metrics.direct_recall)} | {_percent(metrics.paraphrase_recall)} | "
            f"{_percent(metrics.irrelevant_rejection_rate)} | "
            f"{_percent(metrics.abstention_rate)} |"
        )
    lines.extend(
        [
            "",
            "The lexical candidate trades paraphrase recall for exact-source precision and safe "
            "abstention. Embeddings remain intentionally disabled.",
            "",
            f"Safety gate failures: **{len(report.safety_gate_failures)}**",
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the offline grounded RAG evaluation.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_RAG_FIXTURE)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate_rag(args.fixture)
    except (OSError, ValueError) as exc:
        print(f"RAG evaluation input error: {exc}", file=sys.stderr)
        return 2
    if args.format == "markdown":
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
