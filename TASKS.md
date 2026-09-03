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
- [ ] Add per-category thresholds and confusion reports
- [x] Prevent a deployment when safety-regression gates fail

## M2 — Better routing and duplicate detection

- [ ] Import a versioned department/work-assignment catalog
- [ ] Add district/jurisdiction rules and location confirmation
- [ ] Add text + distance + time duplicate scoring
- [ ] Introduce a queue for expensive AI calls

## M3 — RAG and officer console

- [ ] Store document metadata, effective dates, approvals, and supersession links
- [ ] Add hybrid retrieval and sentence-level citations
- [ ] Reject unsupported draft claims
- [ ] Add role-based officer login and immutable review history

## M4 — Production hardening

- [ ] PostgreSQL, migrations, backups, retention jobs
- [ ] Object storage and malware scanning for attachments
- [ ] KMS/secret manager, SSO, RBAC, audit export
- [ ] Observability without complaint-body logging
- [ ] Accessibility, multilingual intake, and load tests
- [ ] Formal privacy, security, legal, and records-management reviews
