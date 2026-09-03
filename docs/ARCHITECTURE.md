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

### Retrieval

Markdown files have stable IDs and category metadata. The retriever is intentionally simple for the
starter; production should add effective dates, approver identity, supersession, hybrid retrieval, and
citation validation.
