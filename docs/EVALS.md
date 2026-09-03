# Offline evaluation harness

Never optimize only average classification accuracy. The harness is deterministic, runs only the
`rules` provider, uses an in-memory SQLite database for pipeline checks, and performs no network call.

## Dataset

Dataset version `2026-09-03.v1` contains 228 synthetic cases:

- `evals/fixtures/routing.jsonl`: 96 cases, 12 for each supported routing category.
- `evals/fixtures/urgency.jsonl`: 38 urgent positives and 10 hard negatives.
- `evals/fixtures/pii.jsonl`: 48 synthetic identifier-shaped targets, 12 per supported type.
- `evals/fixtures/abstention.jsonl`: 36 cases that must remain with a person, including 28 sensitive
  welfare, permit, tax, fine, compensation, abuse/violence, and self-harm cases.

Every row has the dataset version and `synthetic: true`. Locations use explicitly fictitious names.
Emails use IANA-reserved `example.com`, `example.org`, or `example.net`; phone-like values use zero
exchanges; resident-number-shaped values use invalid calendar dates. These values test redaction and
must never be interpreted as real complaint data.

## Exact commands

From the repository root with development dependencies installed:

```bash
python -m evals.run
pytest
ruff check .
ruff format --check .
mypy app evals tests
```

`python -m evals.run` prints a JSON report. Exit code 0 means all gates passed, 1 means a metric or
safety gate failed, and 2 means a fixture could not be loaded or validated.

## Metric definitions

- **Routing Top-1 accuracy** = cases whose expected department is candidate rank 1 / all routing
  cases.
- **Routing Top-3 accuracy** = cases whose expected department appears in candidate ranks 1–3 / all
  routing cases. Both metrics are also reported per expected category.
- **Emergency recall** = expected non-normal urgency cases detected as `high` or `critical` / all
  expected non-normal urgency cases. Expected severity and signal labels are checked separately.
- **Emergency false-positive rate** = expected-normal cases detected as non-normal / all
  expected-normal urgency cases.
- **PII masking recall** = synthetic targets for which the exact source token is absent after
  redaction and the expected PII type is reported / all synthetic PII targets. Recall is also
  reported separately for resident-number, email, mobile-phone, and landline-phone shapes.
- **Abstention rate** = abstention cases left unassigned in `needs_review` or `urgent_review` with
  `requires_human_review=true` / all abstention cases.
- **Safety counts** report sensitive automatic assignments/finalizations, urgent automatic
  assignments or wrong queues, and processed complaints missing required audit events.

## CI release gates

These are prototype regression gates, not legal or production-readiness standards:

- At least 200 versioned synthetic cases.
- Routing Top-1 accuracy at least 95% and Top-3 accuracy 100%.
- Emergency recall 100%; emergency false-positive rate at most 10%.
- PII masking recall 100%.
- Abstention rate 100%.
- Sensitive auto-assignment and auto-finalization counts: 0.
- Urgent auto-assignment and wrong-review-queue counts: 0.
- Processed cases missing required audit events: 0.
- Any case-level PII, abstention, emergency miss, severity, signal, queue, or audit failure: 0.

CI runs both the direct safety regression tests and `python -m evals.run`. A failure in either blocks
the workflow.

## Scope and limitations

The current fixtures intentionally exercise the starter rules and known boundaries. Perfect scores on
this synthetic suite do not establish production accuracy. Future versions should add independently
reviewed, lawfully sourced and de-identified examples, per-category thresholds, confusion reports,
transfer-rate labels, and grounded-draft evaluation without weakening the zero-tolerance safety gates.
