"""CLI: export standalone, copy-pasteable teacher prompts for the offline
manual-teacher workflow -- for when no OPENAI_API_KEY is configured and a
human pastes prompts into a subscription ChatGPT session instead of calling
the API.

This is a second front door onto the exact same seed set, the exact same
live agent instructions, and the exact same TeacherGeneration/ChatModelOutput
target schema as evals/chat_training_gen_run.py (the API-backed workflow,
left completely unmodified) -- only how the teacher is *invoked* differs.
evals/chat_training_manual_import.py is the matching other half: it imports
whatever gets pasted back into manual_responses/.

Every exported prompt is fully self-contained: no chat history, no reference
to any other prompt, and never includes eval_dataset/ content, reserved
seeds, or eval targets -- a seed reserved in
eval_dataset/reserved_seed_registry.jsonl is skipped here exactly like the
API workflow skips it, so no prompt is ever generated for it.

Usage:
    python -m evals.chat_training_prompt_export \\
        --seed-file evals/fixtures/chat_training_seed_templates.jsonl \\
        --out-dir dataset/pilot/<run_id> \\
        --run-id <run_id> \\
        --declared-model "ChatGPT Plus web, gpt-5.1"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.services.chat_agent import _INSTRUCTIONS
from evals.chat_training_gen import (
    GENERATION_VERSION,
    SeedTemplate,
    TeacherGeneration,
    load_seed_templates_jsonl,
)
from evals.chat_training_gen_run import DEFAULT_REGISTRY, TEACHER_PROMPT_VERSION
from evals.reserved_seed_registry import load_registry, reserved_seed_ids

VARIANTS_PER_SEED = 2

_RUN_SUBDIRS = ("prompts", "manual_responses", "normalized", "rejected", "review")

_CONSTRAINTS = """
Rules for both variants (do not break these):
- Never invent facts (what/when/where/who) that are not implied by the seed
  scenario below. If the case_type leaves something missing, leave it
  missing in the transcript -- do not guess a plausible-sounding fact to
  fill the gap.
- Write a realistic Korean conversation, folded into a single transcript
  string exactly like the production app does: the citizen's message(s) and,
  where the case_type calls for a follow-up, one short assistant question,
  asked one at a time (never ask more than one question in the same turn).
- Only use fictional/invented personal names, phone numbers, or resident
  registration numbers, and only when case_type is "pii_bearing". Never
  reuse a real person's real PII.
- For a "seongnam" region_scope seed, only reference place names that are
  real, verifiable Seongnam locations already implied by the scenario brief
  below -- never invent a place, and never state or imply a responsible
  department, a law/ordinance citation, an eligibility conclusion, a
  processing deadline, or any administrative promise. Only describe what the
  citizen reported and what they want looked into.
- ready_to_submit=true only once what happened and roughly where are both
  established in the transcript; otherwise ready_to_submit=false.
- Produce exactly two independent variants (variant_id 1 and 2). They must
  be meaningfully different from each other (different phrasing/details),
  not copies -- near-duplicate variants are rejected by the automated
  importer.
""".strip()

_RESPONSE_FORMAT_TEMPLATE = """
Return JSON only -- no prose, no explanation, no Markdown code fence (no
```), nothing before or after the JSON object. The JSON must have exactly
this shape:

{{
  "seed_template_id": "{seed_template_id}",
  "teacher_model": "<the model you are, e.g. gpt-5.1>",
  "variants": [
    {{
      "variant_id": 1,
      "transcript": "<the Korean conversation transcript>",
      "target": {{
        "assistant_message": "<string, max 500 chars>",
        "title": "<string, max 200 chars; may be empty if ready_to_submit is false>",
        "content": "<string, max 20000 chars; may be empty if ready_to_submit is false>",
        "location_text": "<string, max 300 chars; may be empty if ready_to_submit is false>",
        "ready_to_submit": true
      }}
    }},
    {{
      "variant_id": 2,
      "transcript": "...",
      "target": {{ "...": "same shape as variant 1" }}
    }}
  ]
}}
""".strip()


def render_prompt(seed: SeedTemplate) -> str:
    schema = json.dumps(TeacherGeneration.model_json_schema(), ensure_ascii=False, indent=2)
    scenario = json.dumps(
        {
            "region_scope": seed.region_scope,
            "category": seed.category,
            "case_type": seed.case_type,
            "scenario_brief": seed.scenario_brief,
        },
        ensure_ascii=False,
        indent=2,
    )
    return f"""\
=== TRAINING-DATA GENERATION PROMPT (offline/manual teacher workflow) ===
seed_template_id: {seed.seed_template_id}
Requested variants: 1 and 2 -- produce BOTH, independently. Variant 2 must
not be a copy or a light edit of variant 1.

This prompt is fully self-contained. Do not use any earlier conversation in
this chat, any other prompt, or any information beyond what is written
below.

--- Live production assistant instructions (for tone/behavior reference) ---
{_INSTRUCTIONS}

--- Target JSON schema (each variant's "transcript"/"target" must match this) ---
{schema}

--- Seed scenario ---
{scenario}

--- Generation constraints ---
{_CONSTRAINTS}

--- Output format ---
{_RESPONSE_FORMAT_TEMPLATE.format(seed_template_id=seed.seed_template_id)}
"""


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True, cwd=Path(__file__).parent
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export standalone copy-paste prompts for the offline/manual "
        "teacher workflow (no API key required)."
    )
    parser.add_argument("--seed-file", type=Path, action="append", default=[], required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument(
        "--declared-model",
        default="unspecified-subscription-model",
        help="Human-readable name of the subscription model you will paste this "
        "into (e.g. 'ChatGPT Plus web, gpt-5.1'). Recorded in metadata.json only "
        "-- never verified, since there is no API call to verify it against.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        seeds = load_seed_templates_jsonl(args.seed_file)
    except (OSError, ValueError) as exc:
        print(f"Unable to load seed templates: {exc}", file=sys.stderr)
        return 2

    reserved = reserved_seed_ids(load_registry(args.registry))
    exported = [seed for seed in seeds if seed.seed_template_id not in reserved]
    skipped_reserved = [
        seed.seed_template_id for seed in seeds if seed.seed_template_id in reserved
    ]

    run_dir = args.out_dir
    for subdir in _RUN_SUBDIRS:
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)

    for seed in exported:
        (run_dir / "prompts" / f"{seed.seed_template_id}.txt").write_text(
            render_prompt(seed), encoding="utf-8"
        )

    expected_seed_ids = [seed.seed_template_id for seed in exported]
    expected_variant_keys = [
        f"{seed_id}::v{variant_id}"
        for seed_id in expected_seed_ids
        for variant_id in range(1, VARIANTS_PER_SEED + 1)
    ]
    manifest = {
        "run_id": args.run_id,
        "variants_per_seed": VARIANTS_PER_SEED,
        "prompt_version": TEACHER_PROMPT_VERSION,
        "generation_version": GENERATION_VERSION,
        "expected_seed_ids": expected_seed_ids,
        "expected_variant_keys": expected_variant_keys,
        "skipped_reserved_seed_ids": skipped_reserved,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    metadata = {
        "run_id": args.run_id,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "generation_method": "manual_offline_teacher",
        "prompt_version": TEACHER_PROMPT_VERSION,
        "generation_version": GENERATION_VERSION,
        "exported_seed_ids": expected_seed_ids,
        "skipped_reserved_seed_ids": skipped_reserved,
        "declared_subscription_model": args.declared_model,
        "generation_date_utc": datetime.now(UTC).isoformat(),
    }
    (run_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "exported_prompt_count": len(exported),
                "expected_variant_count": len(expected_variant_keys),
                "skipped_reserved_seed_ids": skipped_reserved,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
