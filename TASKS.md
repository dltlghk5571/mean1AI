# Delivery roadmap

## M0 — Included in this starter

- [x] Local intake UI and JSON API
- [x] PII redaction
- [x] Emergency keyword detection
- [x] Rules provider
- [x] Optional OpenAI structured classifier
- [x] Demo knowledge retrieval and grounded template draft
- [x] Human approval endpoint
- [x] Audit events
- [x] Unit/integration tests and CI

## M1 — Evaluation before smarter automation

- [x] Create a versioned, de-identified evaluation dataset with at least 200 examples
- [x] Add top-1/top-3 routing, urgent recall, PII recall, and abstention metrics
- [x] Add per-category thresholds and confusion reports
- [x] Prevent a deployment when safety-regression gates fail

## M2 — Better routing and duplicate detection

- [x] Import a versioned synthetic department/work-assignment catalog with immutable history
- [x] Add local location normalization and human confirmation
- [ ] Add district/jurisdiction rules and an approved coordinate source
- [x] Add text + normalized-location + time duplicate-candidate scoring
- [ ] Add coordinate-distance scoring after privacy and source review
- [ ] Introduce a queue for expensive AI calls

## M3 — RAG and officer console

- [x] Store document metadata, effective dates, approvals, and supersession links
- [x] Add approved/effective lexical retrieval and sentence-level citations
- [x] Compare and add an offline concept hybrid after the lexical safety baseline
- [x] Reject or flag unsupported draft claims
- [x] Add local role-based officer login and append-only review history

## M4 — Production hardening

- [ ] PostgreSQL, migrations, backups, retention jobs
- [ ] Object storage and malware scanning for attachments
- [ ] KMS/secret manager, SSO, RBAC, audit export
- [ ] Observability without complaint-body logging
- [ ] Accessibility, multilingual intake, and load tests
- [ ] Formal privacy, security, legal, and records-management reviews
