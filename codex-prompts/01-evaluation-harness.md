# Codex task: build the evaluation harness

Goal: turn the current prototype fixtures into a versioned, offline evaluation harness before adding
more model autonomy.

Context: read `AGENTS.md`, `docs/EVALS.md`, `app/services/`, and `tests/` first.

Constraints:
- Do not add real complaint data or direct identifiers.
- Keep all evals deterministic and runnable without network access.
- Sensitive cases and urgent cases must never auto-route.
- Avoid a heavy ML framework; use JSONL fixtures and normal Python unless justified.

Implement:
1. `evals/fixtures/*.jsonl` for routing, urgency, PII, and abstention.
2. A typed evaluator that reports top-1/top-3, urgent recall, PII recall, abstention rate, and failures.
3. `python -m evals.run` plus a nonzero exit code when safety gates fail.
4. Unit tests and concise documentation.

Done when `ruff check .`, `ruff format --check .`, `pytest`, and `python -m evals.run` all pass. Review
the final diff against every safety invariant in `AGENTS.md`.
