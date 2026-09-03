# Prototype data policy

## Data classes

1. Direct identifiers: resident-registration number, phone, email.
2. Operational context: complaint text, approximate location, category, routing, draft.
3. Governance data: provider, model, rule hits, document IDs, human approvals, timestamps.

## Prototype rules

- Redact direct identifiers before any AI provider call.
- Do not log complaint title, body, or raw provider prompt at INFO level.
- Use synthetic data in tests and screenshots.
- Store only what is necessary for the demo.
- Do not use prototype records to train a model.
- Delete the local SQLite file to remove all demo data.

## Production gaps

A real deployment needs a formal retention schedule, records classification, encryption, access
control, data-subject procedures, processor agreements, incident response, backup deletion, and legal
review. This document is not a compliance determination.
