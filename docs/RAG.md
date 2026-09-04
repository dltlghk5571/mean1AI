# Grounded RAG v1

This prototype uses a deterministic, offline lexical baseline. It does not call an embedding model,
external search service, government system, or network API. All evaluation queries are synthetic and
fully de-identified.

## Knowledge governance and retrieval

Each file in `app/data/knowledge` must declare all of these front-matter fields:

```yaml
id: KB-STABLE-ID
title: Human-readable title
category: routing_category
version: immutable-version-label
effective_from: 2026-01-01
effective_until: 2099-12-31
approval_status: approved
superseded_by:
```

Dates are ISO 8601 and inclusive. `effective_until` and `superseded_by` may be blank. Allowed approval
states are `approved`, `draft`, and `revoked`. A document is eligible only when it is approved, is
effective on the retrieval date, and has no approved/effective successor. Unknown successor IDs,
duplicate IDs, invalid dates, and missing metadata stop startup instead of silently weakening the
filter.

`strict_lexical_v1` then requires the routing category to match and at least one non-stopword query
token to occur in the title or body. Documents containing direct-identifier shapes, known
prompt-injection instructions, or instructions for unreviewed automatic disposition are excluded.
The current controls are a safety backstop for the synthetic demo, not a complete hostile-document
security system.

## Structured output and citation enforcement

Every draft provider must return `StructuredDraftOutput`, not free-form text. Each item contains:

- `text`: one sentence or operational display line.
- `substantive`: whether the sentence states handling guidance from the knowledge base.
- `source_ids`: the retrieved document IDs supporting that sentence.

For each substantive sentence, the validator requires at least one source ID, rejects any ID outside
the retrieved set, and requires at least two overlapping content tokens with at least 35% lexical
coverage against the cited title/body. Non-substantive operational text must match the small local
allowlist of receipt, classification display, follow-up, and disclaimer forms and must not claim a
knowledge source; this prevents a provider from disguising a claim as operational text. Known
injection or automatic-disposition wording is rejected regardless of citation. Direct-identifier
shapes in provider output are redacted and the sentence is rejected before either accepted or
rejected text is persisted.

Rejected sentences never enter `Complaint.answer_draft`. Any rejection, or the absence of a usable
source, forces `requires_human_review=true`, clears automatic assignment, and leaves a body-free audit
record. The officer screen shows accepted sentence-to-source mappings plus each document's version,
approval, and effective dates. Editing the rendered draft changes its grounding status to
`human_modified_unverified`. No approval action sends a message or finalizes an administrative case.

## Exact commands

Run from the repository root with the development environment installed and `AI_PROVIDER=rules`:

```bash
python -m evals.rag_run
python -m evals.rag_run --format markdown
pytest tests/test_knowledge.py tests/test_grounded_draft.py tests/test_rag_evals.py
pytest
ruff check .
ruff format --check .
mypy app evals tests
python -m evals.run
```

The RAG command returns 0 when all safety gates pass, 1 for a metric/safety regression, and 2 for
invalid input. The JSON report is the CI default; `--format markdown` prints the comparison table.

## Metric definitions

For each query, the predicted set is the retrieved source IDs and the expected set is the synthetic
labels in `evals/fixtures/rag_retrieval.jsonl`.

- **Precision** = correctly retrieved source IDs / all retrieved source IDs. Empty-only results use
  precision 1.0 and are still reflected in recall and abstention.
- **Recall** = correctly retrieved source IDs / all expected source IDs.
- **F1** = harmonic mean of precision and recall.
- **Direct recall** = direct-wording positive cases whose complete expected set was retrieved / all
  direct-wording positive cases.
- **Paraphrase recall** = paraphrased positive cases whose complete expected set was retrieved / all
  paraphrased positive cases.
- **Irrelevant rejection rate** = irrelevant cases returning no source / all irrelevant cases.
- **Abstention rate** = cases returning no source / all cases.

The safety gates require exactly 24 versioned synthetic cases, candidate precision 100%, direct recall
100%, irrelevant rejection 100%, and zero false-positive source IDs. These gates favor missing a draft
over citing an unsupported document.

## Measured lexical trade-off

Dataset `2026-09-04.rag-v1` has 16 direct positives, four paraphrased positives, and four irrelevant
negatives. Results measured on 2026-09-04 are:

| Strategy | Precision | Recall | F1 | Direct recall | Paraphrase recall | Irrelevant rejection | Abstention |
|---|---:|---:|---:|---:|---:|---:|---:|
| Approved/effective category-only baseline | 83.3% | 100.0% | 90.9% | 100.0% | 100.0% | 0.0% | 0.0% |
| `strict_lexical_v1` | 100.0% | 80.0% | 88.9% | 100.0% | 0.0% | 100.0% | 33.3% |

The strict lexical candidate removes all four irrelevant citations but misses all four lexical-gap
paraphrases. This is the intentional v1 trade-off: prefer a visible human-review abstention over an
unsupported citation. Embeddings remain deferred until a later, separately reviewed experiment can
improve paraphrase recall without weakening precision, document governance, or citation validation.
