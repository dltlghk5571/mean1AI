# Grounded RAG v2

This prototype uses deterministic, offline hybrid retrieval. It combines exact-token evidence with a
small reviewed Korean concept map; it does not call an embedding model, external search service,
government system, or network API. All evaluation queries are synthetic and fully de-identified.

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

Every strategy applies governance before relevance ranking. The routing category must match, and
documents containing direct-identifier shapes, known prompt-injection instructions, or instructions
for unreviewed automatic disposition are excluded.

`strict_lexical_v1` remains callable as the frozen comparison baseline. It selects a document when at
least one non-stopword query token occurs exactly in the title or body.

`offline_concept_hybrid_v2` is the runtime default. It maps visible Korean aliases into two or three
reviewed concepts per routing category, such as lighting asset + malfunction or water system + flow
problem. A document is eligible for ranking only when query and document share at least two exact
tokens or two concepts. One ambiguous signal such as `공원` in a performance-schedule query is not
enough. Ranking uses:

```text
hybrid_score = 0.65 * exact-token cosine overlap
             + 0.35 * concept cosine overlap
```

The concept map is static source code, reviewed in Git, and makes no learned or remote inference. It
bridges a narrow set of wording gaps but is not general semantic search. The content controls are a
safety backstop for the synthetic demo, not a complete hostile-document security system.

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
labels in `evals/fixtures/rag_retrieval_v2.jsonl`.

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

The v2 safety gates require exactly 36 versioned synthetic cases, hybrid precision 100%, direct recall
100%, paraphrase recall 100%, irrelevant rejection 100%, zero false-positive source IDs, and hybrid
recall strictly above the frozen lexical baseline. These gates continue to favor abstention over an
unsupported citation.

## Measured comparison

Dataset `2026-09-04.rag-v2` has 16 direct positives, eight paraphrased positives, and 12 irrelevant
negatives. Eight of the negatives deliberately contain one tempting in-category word or concept. The
v1 dataset remains at `evals/fixtures/rag_retrieval.jsonl` for audit comparison; the current evaluator
intentionally accepts v2, whose default fixture is `evals/fixtures/rag_retrieval_v2.jsonl`. Results
measured on 2026-09-04 are:

| Strategy | Precision | Recall | F1 | Direct recall | Paraphrase recall | Irrelevant rejection | Abstention |
|---|---:|---:|---:|---:|---:|---:|---:|
| Approved/effective category-only baseline | 66.7% | 100.0% | 80.0% | 100.0% | 100.0% | 0.0% | 0.0% |
| `strict_lexical_v1` | 84.2% | 66.7% | 74.4% | 100.0% | 0.0% | 75.0% | 47.2% |
| `offline_concept_hybrid_v2` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 33.3% |

On this fixed synthetic set the hybrid recovers all eight wording-gap cases and rejects every
irrelevant query, including the eight single-signal hard negatives. These numbers do not establish
real-world quality: the concept map and fixture are deliberately small. Embeddings remain deferred
until a separately reviewed experiment can demonstrate additional coverage without weakening
precision, document governance, privacy, or citation validation.
