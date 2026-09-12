# Local role authentication and review history

This milestone provides a realistic local authorization boundary without connecting to a government
identity provider or using real staff data. Every bundled account and credential is synthetic and
public. Do not reuse these values for any real service.

## Roles and permissions

| Role | Read complaints/audits | Create/reprocess | Location/duplicate review | Approve draft/route |
|---|:---:|:---:|:---:|:---:|
| `triage_officer` | yes | yes | yes | no |
| `reviewer` | yes | yes | yes | yes |
| `auditor` | yes | no | no | no |

The UI hides actions the current role cannot perform, but server-side checks are authoritative. API
clients cannot choose an actor ID: the server copies `username` and `role` from the verified session.
Unknown JSON fields such as a spoofed `actor_id` are rejected by Pydantic.

Shared field incidents use the same staff session and CSRF boundary: all roles can read
`/staff/incidents`, triage/reviewer roles can create, link, unlink and change field progress,
and only reviewers can publish or withdraw shared citizen guidance. Each command appends an
immutable `IncidentEvent` and per-complaint audits in one SQLite transaction. Citizen visibility
is restricted to their accessible complaint and the current explicitly published snapshot;
membership or progress changes invalidate that snapshot. See [shared incidents](SHARED_INCIDENTS.md).

Deferred AI enqueueing uses the same create/reprocess permissions and CSRF checks. Queue audit
actor IDs come from the authenticated session; request keys do not supply identity. All roles can
read the current AI state and `GET /api/v1/complaints/{id}/ai-processing` history. Claim/complete/fail
have no HTTP endpoints: the worker requires local database access. An approval atomically ends active
AI work with `human_review_superseded`, so a late worker cannot overwrite the human result. Queue
audits are body-free and use the existing append-only guards; provider exception strings and claim
tokens are not exposed in audit details or status responses.

## Session and request protection

- Passwords are checked with PBKDF2-HMAC-SHA256 and per-account synthetic salts.
- The session is a JSON payload signed with HMAC-SHA256. It contains only username, role, expiry, and
  a random CSRF value; it contains no complaint data or password.
- The cookie is `HttpOnly`, `SameSite=Strict`, scoped to `/`, and lasts eight hours. It is also
  `Secure` when `APP_ENV=production`.
- Staff pages (`/staff`, `/complaints/*`) and `/api/v1/complaints*` require a valid signed session.
  Citizen routes have an independent access boundary described below.
- Every state-changing complaint request requires the session's CSRF value. HTML forms submit it in a
  hidden field; API clients use the `X-CSRF-Token` header.
- Development creates an ephemeral signing key when `SESSION_SECRET` is blank, so sessions end when
  the process restarts. Production mode refuses to start without an explicit secret.
- Login redirect targets accept only local absolute paths, preventing external redirect injection.

## Append-only human review history

`Complaint` continues to hold the current workflow projection. Every approval also inserts a
`ReviewDecision` containing the authenticated actor and role, department, answer snapshot, whether
the generated draft changed, grounding status, and timestamp. There are no update or delete routes
for this table.

At startup, SQLite installs `BEFORE UPDATE` and `BEFORE DELETE` triggers on both `review_decisions`
and `audit_events`. The tests issue direct ORM update/delete attempts and require the database to
reject them. A user who owns the database file can still replace the file or drop triggers, so this is
append-only application history, not a cryptographically tamper-evident production ledger.

Officer-edited answer text is checked for the same direct-identifier shapes as intake. A draft with a
resident-number, phone, or email shape is rejected before either the current complaint or review
snapshot is changed.

## Exact local verification

Use only synthetic data and keep the deterministic provider:

```powershell
$env:AI_PROVIDER = 'rules'
pytest tests/test_auth.py
pytest
ruff check .
ruff format --check .
mypy app evals tests
python -m evals.run
python -m evals.rag_run
```

The authentication regression suite covers anonymous access, incorrect passwords, cookie signing,
tampering, CSRF, role escalation, actor spoofing, open redirects, role-specific UI controls,
direct-identifier rejection, logout, production-secret enforcement, and direct database mutation of
append-only rows.

## Deliberate limitations

- No staff SSO, MFA, password reset, account provisioning, lockout, or login rate limiting.
- No central key manager, session revocation store, device binding, or security monitoring.
- Demo credentials are visible on the login page and in this repository.
- Authorization is local to one process and one SQLite file.
- The app remains unsuitable for internet exposure or real complaint/staff data.

The production successor should use the approved municipal identity provider, centrally managed
roles, short-lived server-side sessions, key rotation, login abuse controls, database migrations, and
an independently protected audit store.

## Citizen access and intake

The public middleware allowlist adds only `/` and the `/minwon` namespace. Staff intake, APIs,
approval, audit, and reprocessing remain behind the officer session. Citizen HTML uses an explicit
allowlist projection; ORM complaint objects and internal draft/review/audit fields are never passed
to citizen templates.

`minwon_citizen_session` is a separate random 256-bit opaque cookie, `HttpOnly`, `SameSite=Strict`,
`Secure` in production, and valid for 30 days from creation. `CitizenSession` stores its SHA-256
hash, a random CSRF value and expiry. Unlike ephemeral officer sessions, it survives a process
restart. Citizen POSTs require that cookie and the `X-Citizen-CSRF` header; an officer CSRF token
cannot substitute. Citizen responses use `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

Each submission stores a random receipt number, owner session hash, request UUID and lookup-code
hash. The 100-bit lookup code is derived with HMAC-SHA256 from the owner's random cookie and request
UUID, then displayed as grouped Base32. Only its hash is stored in the database. A valid owner cookie
can redisplay the same code. Another browser must POST both receipt and code; a successful lookup
adds a `CitizenGrant` for that one complaint, never for the owner's other submissions. Receipt codes
are never placed in a URL, audit record or server log. Expired owner cookies are insufficient for
access; receipt/code verification can restore access in a new session. There is no identity-based
recovery if both the original session and lookup code are lost.

Preview validates and redacts locally without inserting a complaint or calling AI. Final intake
uses the existing pipeline with `commit=False`, then saves complaint, ownership and audit in one
transaction. The unique `(owner_session_hash, request_key)` constraint makes retries and concurrent
submissions return the existing receipt. A failure rolls the whole transaction back. The server
does not log exception strings/tracebacks that could contain bound SQL parameters or raw input.
Validation responses contain fixed messages, not submitted values. Request bodies are limited to
160,000 bytes, including chunked uploads.

Citizen POST limits are 5 submissions, 10 lookups and 30 previews per minute per client IP. The
in-memory limiter hashes addresses, has a bounded bucket count, and ignores client-supplied
forwarding headers. This is a single-process demo limit; proxies/multiple workers require shared
abuse controls. Expired session/grant records are not automatically purged. Real deployment also
needs a deliberate retention/migration policy and protected staff authentication; the public demo
credentials continue to allow anyone to act as an officer in this local prototype.

## Explicit citizen reply publication

Only a reviewer with a valid officer CSRF token can POST `/complaints/{id}/publish`. The request
must confirm publication and identify the currently approved `ReviewDecision`. A complaint lock
serializes this with approvals and reprocessing; stale or unreviewed drafts are rejected. The
server copies the immutable review snapshot, not client-submitted answer text, and checks direct
identifier shapes again. A duplicate publication of that review returns the same record.

`PublishedReply` is protected by SQLite append-only UPDATE/DELETE triggers. Publication and its
`citizen_reply_published` audit event commit together. Only the latest explicitly published snapshot
appears to authorized citizen sessions. Internal approval alone never publishes; later edits or
reprocessing cannot change a previous public snapshot. A new approval and explicit publication are
needed for a replacement. This publishes within the local demo only; no message or government
request is sent, no administrative action is decided, and no complaint is closed.

## Optional incident comparison

`/staff/incident-comparisons/{complaint_id}/{candidate_id}` is staff-only. Triage officers and reviewers
may submit a CSRF-protected comparison; auditors can read stored results. The default provider is off.
Club mode sends only the selected records' re-masked text, category, submission time, urgency and field
status. It exposes no complaint/citizen IDs, photos, incident title or members to the model or MCP.
This prototype's pattern redaction is not complete anonymization; use synthetic fixtures only.

Before model access, `incident_comparison_requested` audits commit for both complaints. HTTP runs
outside the SQLite write transaction. A new transaction checks freshness and atomically inserts an
append-only `IncidentComparison` and both final audits (`ready`, `failed` or `stale`). Invalid/stale
responses retain no model prose. If final storage fails, the result is withheld; the initial request
audit may remain. Audits contain request ID, fingerprint, provider/model version, status and
`automatic_link=false`, never input text, evidence, keys or endpoint URLs.

Comparison cannot confirm/reject candidates, change incident membership or publish citizen guidance.
Latest stored suggestions are hidden after input/review/incident revision or provider/model-ID changes.
See [the comparison contract](INCIDENT_COMPARISON.md) for limits, transport and team handoff.

## Club classification and model API scaffold

Club classification requires the existing local deferred queue. Intake/preflight and enqueue audits
commit before the worker claims a job; claim audit commits before HTTP. The job pins provider/model,
catalog and input hash, then completion rechecks them and human-review state. Verified classification,
audit and terminal queue state commit together. No model confidence can bypass required review.
`club_synthetic` distinguishes explicit synthetic responses from `club`; changing that mode or the
model version invalidates pending work. Queue retries preserve the original attempt budget.

The model gateway has no complaint database, session or write tools. It checks Bearer authentication
before reading a bounded JSON body and returns fixed errors without echoing input. The calling app
owns decision audits; model inputs/replies, keys, URLs and exception strings are never audit details.
The gateway is disabled by default, has no real inference backend and starts no listener at import
or factory creation. See [MODEL_GATEWAY.md](MODEL_GATEWAY.md) for the exact synthetic/real boundary.
