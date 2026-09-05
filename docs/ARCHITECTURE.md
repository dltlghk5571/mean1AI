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
  |-- approved/effective offline hybrid knowledge retriever
  |-- structured draft provider
  |-- sentence-level citation validator
  `-- audit recorder
        |
        v
SQLite through SQLAlchemy
  |-- current complaint state
  |-- durable local AI jobs (queued / processing / completed / failed)
  |-- append-only audit events
  |-- append-only review decisions
  |-- current department projection
  `-- append-only catalog versions, entries, and import events
```

## Design decisions

### Citizen portal and officer workspace

`/` is the citizen homepage; `/staff` is the authenticated officer dashboard. Citizen routes under
`/minwon/*` use a separate opaque session/CSRF boundary, owner-scoped access and receipt/code grants.
Only allowlisted redacted fields and explicitly published answers enter citizen templates.

Citizen intake composes the existing `ComplaintPipeline.create_and_process(commit=False)` with
`CitizenSubmission` ownership and audit writes in one transaction. The request UUID is unique per
owner session. A preview invokes only validation and local redaction. Four additive tables implement
citizen sessions, submissions, access grants and published replies; existing complaint columns do
not change. Explicit reviewer publication copies a `ReviewDecision` into an immutable `PublishedReply`
and records an audit event. Internal approval and later AI processing never implicitly publish or
overwrite a citizen-visible reply. See `AUTH_AND_AUDIT.md` for access, limits and demo restrictions.

### Provider boundary

`Classifier` is a protocol. The deterministic rules provider keeps local development and CI offline.
The OpenAI provider returns the same Pydantic schema, so routing policy is independent of the model.

### Optional local deferred AI queue (M2)

`AI_DEFERRED_ENABLED=false` preserves synchronous processing. With the flag enabled and
`AI_PROVIDER=openai`, intake runs redaction, emergency/sensitive policy, deterministic rules, local
retrieval and the local citation-enforced draft immediately. Urgent/sensitive cases skip expensive
work with an `ai_job_skipped` audit event. Eligible records receive an `AIProcessingJob` in the same
transaction as intake and preflight audits, and remain available for human review. `rules` always
stays synchronous and offline regardless of the flag. The worker processes classification and draft
preparation through the existing provider/grounding boundaries; the bundled draft provider remains
local and deterministic.

This queue is limited to development/test with a local, file-backed SQLite database. There is no
broker, cloud queue, callback, job-control HTTP endpoint or external government/message integration.
`python -m app.worker --once` performs at most one ready attempt; `--watch` polls until Ctrl+C. The
worker and web app must use the same database, provider/model configuration and catalog. Worker
startup adds missing tables/append-only guards but does not import or replace a catalog; start the
web app first to initialize the catalog. A fresh process can resume committed jobs.

`AIProcessingJob` is an additive table, requiring no alteration of existing complaint columns. It
stores only IDs, state, request key, counters, timing/lease metadata, provider/model configuration,
catalog provenance and a fingerprint of redacted input. It stores no body, prompt, draft, provider
response, credentials or exception text. A unique `(complaint_id, request_key)` constraint gives
request idempotency; a unique nullable active-complaint slot prevents multiple active jobs across
processes. An additive `AIProcessingRequest` table remembers keys coalesced into an active job, so
retrying one of those requests after completion also cannot schedule another call. Existing complaint
reads expose the latest job under `ai_processing` (null for records
without jobs). The authenticated history endpoint returns safe job metadata, excluding claim tokens
and input fingerprints.

State transitions are explicit:

```text
enqueue -> queued -> claim -> processing -> complete -> completed
                              |-- fail with attempts left -> queued (backoff)
                              `-- exhausted/non-retryable -> failed (human review)
queued/processing -> human approval -> failed (human_review_superseded)
```

The claim is one conditional SQL UPDATE with a selected eligible ID, a fresh token and a lease. It
increments the attempt counter and records `ai_job_claimed` in the same transaction. Failed attempts
record a fixed reason code and `ai_job_attempt_failed`; exhausted attempts also record `ai_job_failed`.
The default maximum is three attempts with 30, then 60 seconds of backoff. Limits and base backoff
are persisted per job; restarting or changing settings does not reset the budget. SDK retries are
disabled in deferred mode, and the existing classifier timeout is 30 seconds. A default 120-second
lease is recovered on the next worker invocation, with expiry consuming an attempt and respecting
the same backoff/limit. No sleep-based tests or in-memory queue are required.

Provider execution holds no database transaction or write lock. Redaction runs again at the worker
boundary; untrusted model reason text never becomes an audit reason, and direct identifiers in
classification text are masked. Completion rechecks the token, lease, input, current/effective
catalog, provider/model settings and human-review state under a short SQLite write lock. Complaint
results, grounding, local duplicate processing, audit events and `completed` commit atomically. A
failure rolls back partial results and retains the local preflight information for human review.
Deferred completion always requires human review, regardless of confidence.

SQLite does not implement `SELECT FOR UPDATE`, so projection-changing review/reprocess/completion
paths serialize with a no-op complaint UPDATE before refreshing state. An officer's approval
invalidates any active claim within the approval transaction. A later worker completion/failure
cannot overwrite that approval, its draft or append-only review history. Changed catalog/configuration
or input fails closed; work from an expired lease is discarded. A new explicit reprocess request can
start a fresh bounded job after completion/failure, while repeated keys remain idempotent. Existing
keys do not create a new analysis or reopen an already reviewed record.

Execution is **at least once** across crashes: a provider call can occur again if a process stops
after inference but before committing. Claim fencing provides one committed result per job; it
cannot guarantee exactly-once billing at an external AI provider. Leases must exceed the expected
combined provider duration. SQLite write contention, lack of heartbeat renewal, deployment-grade
scheduling, schema migrations and distributed operation remain outside this local milestone.

Queue controls reuse authenticated intake/reprocess permissions and CSRF protection. Claim/complete/
fail are internal worker operations available only to local code with database access, not officer
HTTP endpoints. Auditors can read states/history but cannot enqueue/reprocess/approve. Queue events
use the existing append-only `AuditEvent` guards and contain only fixed codes, IDs and counts. The
officer UI shows both workflow state and AI state; `failed` may coexist with `reviewed` when human
approval superseded a job. Neither AI completion nor worker failure closes, sends or acts on any
external record.

### Policy after inference

The provider may recommend a route, but `ComplaintPipeline` applies non-model policy afterward. This
prevents model confidence from bypassing sensitive-category and emergency rules.

### Versioned synthetic work-assignment catalog

Startup strictly validates the local `departments.json` envelope, effective window, synthetic and
approval flags, stable IDs, and all routing-rule-to-work-assignment references. The rules provider
builds its routing table from that catalog; it does not keep a second hard-coded department keyword
map. Every candidate is rebound to an active catalog department and receives the source catalog
version and allowed work-assignment IDs before workflow policy runs.

Each first-seen version is copied to immutable `DepartmentCatalogVersion` and
`DepartmentCatalogEntry` rows. `CatalogImportEvent` records the source SHA-256 and a body-free change
summary. Reusing a version for different bytes fails closed, while loading identical bytes is
idempotent only for the current version. Successors must name the current version in `supersedes`;
backdated successors, retired IDs and changes to stable ID ownership are rejected. Import event IDs
define the sequence. The import service commits once and rolls back all changes on failure.
SQLite triggers reject updates and deletes on all three catalog history tables. The
mutable `Department` table is only the latest projection, so existing complaint foreign keys keep
stable IDs while historical routing can be reconstructed from the candidate and audit snapshots.

Each new import returns outstanding automatic assignments to human review with
`catalog_route_invalidated` audit events, preserving their original candidate snapshots and all human
approval history. Routing and approval recheck the effective window and current imported version.
Invalid provider references, mismatched categories, missing information and ambiguous candidates
require human review with reason codes in `routing_review_required` and `triage_completed`.
The OpenAI adapter uses the same validation without silently discarding invalid candidates first.

The catalog is fully synthetic and loaded from disk. It does not establish real jurisdiction and
does not call any government or external service. See `docs/DEPARTMENT_CATALOG.md`.

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
disposition content before relevance ranking. The runtime hybrid requires two exact-token or reviewed
category-concept signals, then blends token and concept cosine overlap. The frozen lexical strategy
remains available to the evaluation harness. No embedding service or network call is used.

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
