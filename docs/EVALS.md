# Evaluation plan

Never optimize only average classification accuracy.

## Required datasets

- Routing: de-identified complaints with final handling group and transfer history.
- Urgency: safety-positive and hard-negative examples.
- PII: synthetic boundary cases for spacing, hyphens, and mixed Korean/English text.
- Abstention: ambiguous, multi-issue, out-of-jurisdiction, and novel complaints.
- Draft grounding: source-supported and unsupported claim probes.

## Metrics

- Routing top-1 accuracy and top-3 recall by category.
- Post-routing transfer rate.
- Urgent-signal recall, with false-positive rate reported separately.
- PII redaction recall by identifier type.
- Human-review rate and correctness among auto-route candidates.
- Draft unsupported-claim rate and source freshness.

## Suggested release gates

These are project targets, not legal standards:

- Urgent recall: 100% on the maintained critical test set.
- PII recall: 100% on the maintained direct-identifier test set.
- Sensitive-case auto-route count: 0.
- Un-audited state transition count: 0.
- No statistically meaningful regression in routing top-3 recall.
