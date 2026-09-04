# Architecture

```text
Browser / API client
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
  |-- local knowledge retriever
  |-- grounded template drafter
  `-- audit recorder
        |
        v
SQLite through SQLAlchemy
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
management system.

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

### Retrieval

Markdown files have stable IDs and category metadata. The retriever is intentionally simple for the
starter; production should add effective dates, approver identity, supersession, hybrid retrieval, and
citation validation.
