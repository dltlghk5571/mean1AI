"""CLI: export `app.chat_eval` log lines + `citizen_chat_draft` audit events into
normalized, leakage-safe JSONL training/eval splits.

Usage:
    python -m evals.chat_dataset_run --log-file app.log --out-dir dataset/chat_draft_v1
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import make_engine, make_session_factory
from app.models import AuditEvent
from evals.chat_dataset import (
    AuditDraftRow,
    ChatDatasetResult,
    RejectedRecord,
    build_dataset,
)

DEFAULT_SEED = 20260907


def load_audit_draft_rows(settings: Settings) -> tuple[list[AuditDraftRow], list[RejectedRecord]]:
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    rows: list[AuditDraftRow] = []
    rejected: list[RejectedRecord] = []
    with session_factory() as db:
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "citizen_chat_draft")
        ).all()
        for event in events:
            try:
                rows.append(
                    AuditDraftRow(
                        complaint_id=event.complaint_id,
                        draft_id=event.details.get("draft_id", ""),
                        edited=bool(event.details.get("edited")),
                    )
                )
            except ValidationError:
                rejected.append(
                    RejectedRecord(
                        reason="audit_row_invalid",
                        detail=(
                            f"complaint_id={event.complaint_id} has malformed "
                            "citizen_chat_draft details"
                        ),
                    )
                )
    engine.dispose()
    return rows, rejected


def _write_jsonl(path: Path, items: Sequence[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n")


def write_dataset(result: ChatDatasetResult, out_dir: Path) -> None:
    grouped: dict[str, list] = defaultdict(list)
    for record in result.records:
        key = record.category if record.split is None else f"{record.category}/{record.split}"
        grouped[key].append(record)
    for key, group in grouped.items():
        _write_jsonl(out_dir / f"{key}.jsonl", group)
    _write_jsonl(out_dir / "rejected.jsonl", result.rejected)
    rendered_summary = json.dumps(
        result.summary.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    )
    (out_dir / "summary.json").write_text(rendered_summary + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export chat-draft eval logs into leakage-safe JSONL training splits."
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        action="append",
        default=[],
        required=True,
        help="app.chat_eval log file (repeatable).",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--database-url", default=None, help="Override Settings.database_url for the audit query."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = (
        get_settings() if args.database_url is None else Settings(database_url=args.database_url)
    )

    lines: list[str] = []
    for path in args.log_file:
        try:
            lines.extend(path.read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            print(f"Unable to read log file {path}: {exc}", file=sys.stderr)
            return 2

    audit_rows, audit_rejected = load_audit_draft_rows(settings)
    result = build_dataset(lines, audit_rows, seed=args.seed, extra_rejected=audit_rejected)
    write_dataset(result, args.out_dir)

    print(
        json.dumps(
            result.summary.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
