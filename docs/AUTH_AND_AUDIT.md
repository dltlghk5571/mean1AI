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

## Session and request protection

- Passwords are checked with PBKDF2-HMAC-SHA256 and per-account synthetic salts.
- The session is a JSON payload signed with HMAC-SHA256. It contains only username, role, expiry, and
  a random CSRF value; it contains no complaint data or password.
- The cookie is `HttpOnly`, `SameSite=Strict`, scoped to `/`, and lasts eight hours. It is also
  `Secure` when `APP_ENV=production`.
- All complaint pages and `/api/v1/complaints*` routes require a valid signed session.
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

- No SSO, MFA, password reset, account provisioning, lockout, or rate limiting.
- No central key manager, session revocation store, device binding, or security monitoring.
- Demo credentials are visible on the login page and in this repository.
- Authorization is local to one process and one SQLite file.
- The app remains unsuitable for internet exposure or real complaint/staff data.

The production successor should use the approved municipal identity provider, centrally managed
roles, short-lived server-side sessions, key rotation, login abuse controls, database migrations, and
an independently protected audit store.
