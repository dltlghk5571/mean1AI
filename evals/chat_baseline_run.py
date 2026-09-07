"""CLI: run the current chat-drafting model against a held-out test split and
save baseline metrics, before any LoRA fine-tuning.

Usage:
    python -m evals.chat_baseline_run \
        --test-file dataset/chat_draft_v1/accepted_unedited/test.jsonl \
        --test-file dataset/chat_draft_v1/accepted_edited/test.jsonl \
        --out dataset/chat_draft_v1/baseline_metrics.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from app.config import get_settings
from app.services.chat_agent import ChatAgent
from evals.chat_baseline import run_baseline
from evals.chat_dataset import ChatModelOutput, DatasetRecord


def _load_records(paths: Sequence[Path]) -> list[DatasetRecord]:
    records: list[DatasetRecord] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(DatasetRecord.model_validate_json(line))
    return records


def predict_with_agent(agent: ChatAgent, record: DatasetRecord) -> ChatModelOutput:
    """Re-run just the extract+safety_gate graph on an already-redacted,
    already-collected transcript -- no history replay, since only the final
    joined transcript is exported (per the "no conversation storage" design)."""
    transcript = record.messages[-1]["content"]
    result = agent.graph.invoke(
        {
            "transcript": transcript,
            "assistant_message": "",
            "title": "",
            "content": "",
            "location_text": "",
            "ready": False,
            "draft_id": None,
        }
    )
    return ChatModelOutput(
        assistant_message=result["assistant_message"],
        title=result["title"],
        content=result["content"],
        location_text=result["location_text"],
        ready_to_submit=result["ready"],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the current chat-drafting model against a held-out test split."
    )
    parser.add_argument("--test-file", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    if settings.openai_api_key is None:
        print("OPENAI_API_KEY is not configured; cannot run a live baseline.", file=sys.stderr)
        return 2

    agent = ChatAgent(
        api_key=settings.openai_api_key.get_secret_value(), model=settings.openai_model
    )
    records = _load_records(args.test_file)
    report = run_baseline(records, lambda record: predict_with_agent(agent, record))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    )
    args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
