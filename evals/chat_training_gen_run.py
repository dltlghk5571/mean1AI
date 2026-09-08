"""CLI: run the teacher model over a seed-template batch to produce a
draft-status LoRA training pilot for the ChatAgent's narrow contract.

Usage:
    python -m evals.chat_training_gen_run \
        --seed-file evals/fixtures/chat_training_seed_templates.jsonl \
        --out-dir dataset/chat_training_pilot_v1

Every seed reserved in eval_dataset/reserved_seed_registry.jsonl is skipped
without ever calling the teacher (see evals/chat_training_gen.py). Every
emitted record has provenance.review_status="draft" -- nothing here is
train-ready until a human approves it and evals/chat_training_select.py
selects it (see docs/CHAT_EVAL_DATA_GUIDELINE.md for the mirror-image process
on the evaluation side). excluded_safety_signal records are written to their
own file alongside sft_candidate, never merged into it, and both are counted
in summary.json -- nothing is silently dropped.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import get_settings
from app.services.classifier import ClassifierError
from evals.chat_training_gen import (
    GENERATION_VERSION,
    SeedTemplate,
    TeacherCall,
    TeacherGeneration,
    TrainingGenerationResult,
    generate_pilot,
    load_seed_templates_jsonl,
)
from evals.reserved_seed_registry import load_registry, reserved_seed_ids

DEFAULT_REGISTRY = Path("eval_dataset/reserved_seed_registry.jsonl")

# Bump this whenever _TEACHER_INSTRUCTIONS below changes wording/behavior, even
# if GENERATION_VERSION (the overall pipeline version) doesn't move -- it is
# stamped into every record's provenance.prompt_version so a later regression
# can be traced to a specific prompt revision, not just "some run in 2026."
TEACHER_PROMPT_VERSION = "chat-teacher-prompt.2026-09-08.v1"

_TEACHER_INSTRUCTIONS = """
You generate training examples for a Korean municipal civil-complaint drafting
assistant. Given a scenario brief (region_scope, category, case_type), invent
a short, realistic Korean transcript of what a citizen might type (folding in
a brief prior assistant follow-up into the same transcript string if natural,
matching how the real app joins turns into one string) for that case_type.
Never use real people's names, real phone numbers, or real resident
registration numbers -- invent clearly fictional ones only if case_type is
pii_bearing.

Then produce the exact target the drafting assistant should output for that
transcript:
- ready_to_submit=true only once what happened and roughly where are established.
- If case_type is "incomplete", the transcript must be missing at least one of
  what/when/where, and ready_to_submit must be false with assistant_message
  asking one concise follow-up question.
- If case_type is "ambiguous", the transcript should raise more than one
  distinct issue or an unclear request; ask a clarifying question rather than guess.
- If case_type is "emergency", describe a genuinely urgent situation (fire,
  gas leak, collapse, person in danger, etc.).
- If case_type is "policy_sensitive", raise a welfare eligibility, tax,
  permit, fine, or self-harm topic.
- Never write a department name, a law citation, an eligibility conclusion, a
  processing deadline, or a promised outcome into title/content -- only what
  the citizen described and what they want looked into.
- title/content/location_text must be polite, concrete Korean complaint prose
  derived only from the transcript.
""".strip()


def build_teacher_call(client: Any, model: str) -> TeacherCall:
    def _call(seed: SeedTemplate) -> TeacherGeneration:
        response = client.responses.parse(
            model=model,
            instructions=_TEACHER_INSTRUCTIONS,
            input=json.dumps(
                {
                    "region_scope": seed.region_scope,
                    "category": seed.category,
                    "case_type": seed.case_type,
                    "scenario_brief": seed.scenario_brief,
                },
                ensure_ascii=False,
            ),
            text_format=TeacherGeneration,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ClassifierError("teacher model returned no parsed output")
        return parsed

    return _call


def _write_jsonl(path: Path, items: Sequence[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n")


def write_result(result: TrainingGenerationResult, out_dir: Path) -> None:
    grouped: dict[str, list] = defaultdict(list)
    for record in result.records:
        grouped[f"{record.provenance.region_scope}/{record.bucket}"].append(record)
    for key, group in grouped.items():
        _write_jsonl(out_dir / f"{key}.jsonl", group)
    _write_jsonl(out_dir / "rejected.jsonl", result.rejected)
    rendered_summary = json.dumps(
        result.summary.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    )
    (out_dir / "summary.json").write_text(rendered_summary + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a draft-status LoRA training pilot from seed templates."
    )
    parser.add_argument("--seed-file", type=Path, action="append", default=[], required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--model", default=None, help="Override Settings.openai_model")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    if settings.openai_api_key is None:
        print("OPENAI_API_KEY is not configured; cannot run generation.", file=sys.stderr)
        return 2

    try:
        seeds = load_seed_templates_jsonl(args.seed_file)
    except (OSError, ValueError) as exc:
        print(f"Unable to load seed templates: {exc}", file=sys.stderr)
        return 2

    reserved = reserved_seed_ids(load_registry(args.registry))

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=30.0)
    model = args.model or settings.openai_model
    result = generate_pilot(
        seeds,
        build_teacher_call(client, model),
        reserved_seed_ids=reserved,
        teacher_model=model,
        generation_version=GENERATION_VERSION,
        prompt_version=TEACHER_PROMPT_VERSION,
    )
    write_result(result, args.out_dir)
    print(
        json.dumps(
            result.summary.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
