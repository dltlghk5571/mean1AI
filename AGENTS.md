# AGENTS.md

## Product goal

The citizen conversation is the main product. Help Seongnam residents who are unfamiliar with digital
administration get grounded information or prepare, confirm, submit and follow up on a complaint
without understanding department names or complex forms. Reduce repeated clarification and field work
for staff. Classification, staff tools, data collection, model APIs and MCP support this outcome.

Read [GOALS.md](GOALS.md) for G0 and its intermediate goals, then [TASKS.md](TASKS.md) for the current
small goal. The latest user instruction takes precedence; update these documents when scope changes.

This repository is a prototype. Department names and knowledge documents are demo data and must not be
presented as the current official organization or policy of Seongnam City.

## Goal discipline

- Before working, identify `G0 -> intermediate goal -> one small goal`, its deliverable, completion
  evidence, exclusions and dependencies. Use the existing TASKS entry; do not invent a parallel plan.
- A new issue belongs in the current work only when it blocks that completion condition or is a
  necessary correctness/safety fix for the change. Otherwise record it as a later candidate.
- Do not expand a local task into a new platform, model or broad refactor without explaining its
  necessity for the parent goal. Continue routine decisions already authorized by the user.
- If external inputs are missing, record the exact dependency and return to an available parent-goal
  task. Do not repeatedly build substitute adapters, mock servers or documents.
- Stop when the scoped result and required checks pass. Report which parent outcome improved,
  remaining gaps and one next priority. Test count and synthetic demos are not proof of user benefit.
- Do not start local web, model or MCP servers unless the user requests that work. Do not send
  messages to other project conversations. Use only authorized project context and memory.
- If separate agent work is explicitly requested, assign a bounded scope, parent goal, deliverable
  and termination condition; collect results and verify termination before ending the parent work.

## Non-negotiable safety invariants

1. Never automatically reject, close, penalize, or legally decide a complaint.
2. Welfare eligibility, permits, taxes, fines, compensation, abuse, self-harm, and other high-impact
   matters always require human review.
3. Never send external messages or mutate external government systems without a separate explicit
   approval step.
4. Never include unredacted direct identifiers in model prompts or application logs. Test fixtures may
   use clearly synthetic identifiers only; never copy real complaint data into the repository.
5. Every AI/rules decision and every human approval must create an `AuditEvent`.
   Before citizen intake exists, use the equivalent append-only `CitizenChatAuditEvent` journal;
   link final citizen confirmation to both journals atomically. Never fabricate a complaint to log a chat.
   Public-service data imports and human publication/withdrawal use the equivalent append-only
   `ServiceCatalogReview` journal, bound to the immutable catalog version and content hash.
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
