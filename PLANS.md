# Console usability refinement — 2026-09-05

## Goal

Show the complaint list in the first desktop viewport, make local search actually hide non-matches,
and make long complaint details and the mobile navigation easier to operate.

## Safety and scope

Only presentation, local list ordering and keyboard interaction change. Keep all server-side
classification, review authorization, redaction and audit invariants. Use synthetic records only,
`AI_PROVIDER=rules`, and no external services. Do not persist search terms or draft text in the browser.

## Current behavior

FastAPI renders Jinja templates; `app/static/app.js` enhances forms and the queue. The server loads
the latest 30 records for the selected status. CSS sets queue rows to `display: grid`, overriding
the browser's default `[hidden]` rule: a zero-result search updates the count but leaves all rows
visible. The dashboard's sidebar cards also squeeze the list below the first viewport. Detail
sections have no navigation, and mobile navigation has no focus containment or Escape handling.

## Implementation slices

1. `index.html`, `app.js`, `app.css`: restore hidden-row behavior, clear search, transient sorting,
   count announcements and a full-width, compact queue. State the latest-30-record scope explicitly.
2. `complaint_detail.html`, `app.js`, `app.css`: sticky section navigation, complete metadata wrapping,
   honest static safety guidance and disabled candidate choices for read-only roles.
3. `base.html`, `app.js`, `app.css`: mobile focus containment, Escape and proper asset cache versions.
4. `README.md`, `docs/UI.md`: current walkthrough, limits and repeatable browser regression steps.
5. Review the focused diff and commit after verification.

## Verification

Run `.venv\Scripts\python.exe -m ruff check .`,
`.venv\Scripts\python.exe -m ruff format --check .`,
`.venv\Scripts\python.exe -m mypy app evals tests`,
`.venv\Scripts\python.exe -m pytest`, `node --check app/static/app.js` and `git diff --check`.
Browser checks: rendered row count for zero/partial/reset searches, sorting while filtered,
keyboard search, detail section links and no horizontal overflow at 390, 768, 1024 and desktop widths.
Verify mobile menu focus and Escape, draft preview and reviewer/auditor presentation with demo roles.

## Decisions and surprises

Keep search in memory only; sorting changes the currently rendered latest-30-record subset, never
complaint state. Update existing CSS rules rather than adding another full visual override layer.

## Verification result

- Ruff lint/format and MyPy passed; JavaScript syntax and Git whitespace checks passed.
- Full Python suite: 97 passed, 2 warnings, 34.20 seconds (`AI_PROVIDER=rules`). As in the prior run,
  pytest used approved local permissions because Windows sandbox ACLs block its temporary folders.
- Browser search: zero results rendered zero rows; reset restored 11; partial search rendered 6.
  Oldest sorting reversed the filtered results; priority sorting grouped pending reviews first.
- Queue and detail layouts had no document-level horizontal overflow at 390, 768, 1024 and 1440px.
  Detail anchor headings landed below the sticky navigation.
- Mobile menu Tab/Shift+Tab wrapped within the menu and Escape restored the trigger. Read-only
  auditor candidates were disabled and the draft readonly; no approval form or dialog was rendered.
- Synthetic example fill/cancel, edited-draft preview, candidate selection and approval cancellation
  passed without saving a complaint/review. The inspected audit count stayed at seven events.
- Browser warning/error log was empty. `/health` returned `ok` with `classifier=rules`.
- Temporary viewport overrides were reset. No new dependencies or external services were used.
