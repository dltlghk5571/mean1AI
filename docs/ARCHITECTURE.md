# Architecture

```text
Browser / API client
        |
        v
signed session middleware
  |-- role permission
  `-- CSRF token on mutations
        |
        v
FastAPI route
        |
        v
ComplaintPipeline
  |-- PII redactor
  |-- urgent-signal detector
  |-- classifier provider (rules | OpenAI)
  |-- safety policy override
  |-- local location normalizer
  |-- duplicate-candidate scorer
  |-- approved/effective lexical knowledge retriever
  |-- structured draft provider
  |-- sentence-level citation validator
  `-- audit recorder
        |
        v
SQLite through SQLAlchemy
  |-- current complaint state
  |-- append-only audit events
  `-- append-only review decisions
```

## Design decisions

### Provider boundary

`Classifier` is a protocol. The deterministic rules provider keeps local development and CI offline.
The OpenAI provider returns the same Pydantic schema, so routing policy is independent of the model.

### Policy after inference

The provider may recommend a route, but `ComplaintPipeline` applies non-model policy afterward. This
prevents model confidence from bypassing sensitive-category and emergency rules.

### Human approval

Approval is a separate endpoint and audit action. The MVP never calls an external messaging or case
management system. The actor ID and role come only from the verified session. Each approval adds a
new `ReviewDecision` snapshot; it never updates an earlier decision. SQLite triggers reject `UPDATE`
and `DELETE` statements against both `review_decisions` and `audit_events`, including for databases
created before this feature. The current `Complaint` row remains a mutable projection of the latest
workflow state.

### Local authentication boundary

The local demo has three synthetic roles: `triage_officer`, `reviewer`, and `auditor`. PBKDF2 password
verification creates an eight-hour HMAC-SHA256 signed session cookie with `HttpOnly` and
`SameSite=Strict`. Complaint routes require a valid session; mutations additionally require a CSRF
token and an allowed role. Only reviewers can approve, while auditors are read-only. Development uses
an ephemeral signing key unless `SESSION_SECRET` is configured, and production mode refuses to start
without one.

This boundary is intended to make the local interaction and authorization model testable. It is not
SSO, account lifecycle management, rate limiting, MFA, centralized authorization, or a claim of
production readiness. See `docs/AUTH_AND_AUDIT.md`.

### Location confirmation and duplicate candidates

The application normalizes only the already-redacted free-text location. It performs Unicode,
punctuation, and whitespace normalization locally; it does not call a map, geocoder, jurisdiction,
or government API. A separate officer action confirms that the normalized text is suitable for the
comparison. Confirmation does not assert that the address is real or inside a jurisdiction.

After triage, the duplicate scorer compares the complaint with records inside a 30-day window. A
candidate must have the same category and exact normalized-location key. Its transparent score is:

```text
total = category match * 0.30
      + location match * 0.40
      + time proximity * 0.15
      + redacted text bigram Jaccard similarity * 0.15
```

Only scores at or above 0.70 are retained, up to five candidates. `DuplicateCandidate` stores the
component scores, evidence, version, and officer decision. `ComplaintLocationReview` stores the local
normalization and confirmation state. Both new tables are additive so an existing prototype SQLite
database can be opened without altering the `complaints` table.

Scoring and every human confirmation/rejection create audit events. A duplicate decision never
changes either complaint's workflow status or department, and never merges, closes, or finalizes a
record.

### Grounded retrieval and draft validation

Every Markdown knowledge document has a stable ID, category, version, inclusive effective-date
window, approval status, and optional `superseded_by` link. Retrieval fails during startup for broken
metadata or unknown supersession targets. At request time it excludes non-approved, not-yet-effective,
expired, actively superseded, category-mismatched, known instruction-injection, and automatic-
disposition content before ranking by lexical overlap. No embedding service or network call is used.

The draft provider returns `StructuredDraftOutput`: an ordered list of sentences, a substantive flag,
and source IDs. `CitationEnforcedDrafter` accepts a substantive sentence only when every source ID is
among the retrieved documents and the cited text provides sufficient lexical support. Unsafe,
unmapped, unknown-source, and unsupported sentences are removed from the rendered draft and force
human review. Accepted substantive sentences display inline source IDs.

`GroundedDraftRecord` stores the current structured sentences, rejected-sentence reasons, selected
document metadata snapshot, and retrieval exclusions separately from `Complaint`. This additive table
keeps existing prototype SQLite files usable. Retrieval and validation each create body-free audit
events. If an officer edits the generated text, its status becomes `human_modified_unverified`; the
human approval remains a separate audit event and never sends the response externally. See
`docs/RAG.md` for metrics and exact verification commands.
