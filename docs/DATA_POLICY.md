# Prototype data policy

## Data classes

1. Direct identifiers: resident-registration number, phone, email.
2. Operational context: complaint text, approximate location, category, routing, draft.
3. Derived operational context: redacted normalized location and transparent duplicate scores.
4. Governance data: provider, model, rule hits, document IDs, human approvals, timestamps.

## Prototype rules

- Redact direct identifiers before any AI provider call.
- Do not log complaint title, body, or raw provider prompt at INFO level.
- Use synthetic data in tests and screenshots.
- Store only what is necessary for the demo.
- Retrieve only approved, currently effective, non-superseded local knowledge documents.
- Load only an explicitly approved, effective, synthetic department catalog; preserve each imported
  version, checksum, work-assignment snapshot, and body-free change summary as append-only history.
- Persist source IDs and document-governance metadata snapshots, not hidden prompts or raw model
  reasoning. Exclude unsupported or unsafe provider sentences before rendering a draft.
- Derive human actor IDs from the signed session, never from editable form or JSON fields.
- Restrict complaint records and review history to authenticated demo roles. Keep approval snapshots
  append-only and reject direct-identifier shapes in officer-edited drafts before storage.
- Compute duplicate candidates only from redacted text and local records; never send location or
  complaint data to a map or other external service.
- Treat duplicate confirmation as a review annotation only. It must not merge, close, assign, or
  otherwise dispose of a complaint.
- Do not use prototype records to train a model.
- Delete the local SQLite file to remove all demo data.

## Production gaps

A real deployment needs a formal retention schedule, records classification, encryption, access
control, data-subject procedures, processor agreements, incident response, backup deletion, and legal
review. This document is not a compliance determination.
