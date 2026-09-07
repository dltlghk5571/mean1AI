# Prototype data policy

## Data classes

1. Direct identifiers: resident-registration number, phone, email.
2. Operational context: complaint text, approximate location, category, routing, draft.
3. Derived operational context: redacted normalized location and transparent duplicate scores.
4. Governance data: provider, model, rule hits, document IDs, human approvals, timestamps.

## Prototype rules

- Redact direct identifiers before any AI provider call.
- Citizen chat also redacts these identifier shapes before storing messages and drafts. Names,
  addresses and all identifying prose are not comprehensively detected; use synthetic examples only.
- Chat context contains no session token, CSRF token, receipt lookup code or owner identifier.
- Club HTTP requests are opt-in, redact context again, and send at most 12 recent messages plus
  the current draft and tool results. Credentials remain in a server-side SecretStr and Bearer header.
  Redirects, compressed responses and raw response logging are disabled. No training export occurs.
- Before intake, append-only `CitizenChatAuditEvent` records contain metadata, not conversation text.
  Final consent creates both a linked complaint `AuditEvent` and chat event in the intake transaction.
- One working chat is stored per citizen session. Reset replaces its redacted draft/history while
  preserving metadata audit events and submitted complaints. Session expiry limits access; it does
  not delete stored data. No automatic retention cleanup or model-training export is implemented.
- Do not log complaint title, body, or raw provider prompt at INFO level.
- Use synthetic data in tests and screenshots.
- Optional citizen photos stay in page memory until final confirmation. Reloading clears selections.
  Confirmed photos are decoded with bounded file/pixel limits and re-encoded on a fresh JPEG canvas;
  source filenames and embedded metadata are discarded. Pixels are not automatically anonymized.
  Use synthetic photos without identifiers. Images never enter chat state, model context, or logs.
  Store photos with intake in one database transaction. Reads require complaint-specific citizen
  access or the existing officer session. Session expiry does not delete stored photos.
- Store only what is necessary for the demo.
- Citizen follow-ups store only redacted text with a keyed request fingerprint, safety metadata,
  and append-only audits in one transaction. They never enter model prompts or logs. Only the
  original unexpired citizen session can write; lookup-code grants remain read-only. Explicit
  reviewer publication creates one immutable response per inquiry, with no automatic complaint
  status change. Reject identifier patterns in responses before publication. Names and other
  identifying prose are not comprehensively detected. No retention cleanup or training export
  is implemented. See `CITIZEN_FOLLOWUPS.md` for the full contract and recovery limitations.
- Retrieve only approved, currently effective, non-superseded local knowledge documents.
- Public-service source extraction writes a local review queue; it never publishes or trains.
  Known direct-identifier shapes are redacted during extraction and rejected in catalog imports.
  Source text hashes describe normalized, redacted text, not the original downloaded bytes.
- Preserve unknown publication/effective dates as null. Local imports record ingested_at and do
  not fabricate fetched_at. Retrieval permission and training permission are separate attributes.
- Service catalog versions and human publication/withdrawal decisions are append-only. Only the
  latest approved full snapshot, within its UTC review date and service effective dates, is searched.
  Expiry/withdrawal does not restore an older version. Pending data is excluded from model context.
- Agent tool audits contain tool/version/result IDs and outcome, not queries or raw arguments.
  No tool can read other citizens' complaints, fetch arbitrary URLs, submit or close complaints.
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
