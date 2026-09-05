# AGENTS.md

## Product goal

Build a human-in-the-loop civic complaint triage system. The MVP accepts Korean civic complaints,
redacts direct identifiers before AI processing, detects urgent safety signals, recommends a category
and demo work group, retrieves approved local guidance, drafts a response, and records an audit trail.

This repository is a prototype. Department names and knowledge documents are demo data and must not be
presented as the current official organization or policy of Seongnam City.

## Non-negotiable safety invariants

1. Never automatically reject, close, penalize, or legally decide a complaint.
2. Welfare eligibility, permits, taxes, fines, compensation, abuse, self-harm, and other high-impact
   matters always require human review.
3. Never send external messages or mutate external government systems without a separate explicit
   approval step.
4. Never include unredacted direct identifiers in model prompts or application logs. Test fixtures may
   use clearly synthetic identifiers only; never copy real complaint data into the repository.
5. Every AI/rules decision and every human approval must create an `AuditEvent`.
6. Model confidence never overrides a safety rule.
7. New external dependencies require a short justification in the final Codex summary.
8. Keep the rules-based provider operational so the full test suite runs without network or API keys.

## Repository map

- `app/main.py`: application factory and route registration
- `app/api/`: HTTP and HTML routes
- `app/services/`: redaction, urgency detection, classification, retrieval, drafting, pipeline
- `app/data/departments.json`: demo routing catalog
- `app/data/knowledge/`: approved demo knowledge snippets
- `tests/`: unit and integration tests
- `docs/`: product, architecture, privacy, and evaluation notes
- `codex-prompts/`: scoped prompts for subsequent implementation milestones

## Local commands

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
uvicorn app.main:app --reload
```

## Engineering conventions

- Python 3.11+ with type hints.
- Pydantic models use `ConfigDict(extra="forbid")` for AI structured outputs.
- Business logic belongs in services, not route handlers.
- Database writes happen through an explicit SQLAlchemy session and are committed in one place.
- Persist stable identifiers, not display names, for departments and knowledge sources.
- Use Korean for citizen-facing UI and demo responses; use English identifiers and concise comments.
- Never log complaint bodies at INFO level.
- Prefer deterministic tests; mock any OpenAI calls.

## Git Flow

- Follow `docs/GITFLOW.md`. Start ordinary work from `develop` in a `feature/*` branch and target
  `develop` in its PR. Release branches start from `develop`; hotfix branches start from `main`.
- Release/hotfix changes must reach both `main` and `develop`, plus any affected active release.
- Use merge commits and another team member's review; do not force-push shared branches.
- The initial Git Flow setup may publish the same configuration to existing `main` and new `develop`.
  Subsequent work follows the branch and PR workflow. Branch protection needs repository admin access.

## Definition of done

Before reporting a task complete:

1. Run `ruff check .`.
2. Run `ruff format --check .`.
3. Run `pytest`.
4. Review the diff for violations of the safety invariants.
5. Update the relevant document or test when behavior changes.
6. Summarize changed files, verification performed, and remaining risks.
