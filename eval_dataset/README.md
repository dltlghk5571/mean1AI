# eval_dataset/

The Seongnam chat-drafting gold evaluation set. Authored and reviewed
independently of the training-data pipeline in `evals/chat_training_gen*.py` --
see `docs/CHAT_EVAL_DATA_GUIDELINE.md` for the full authoring guideline,
schema reference, case-type coverage requirements, review checklist,
leakage-prevention rules, and versioning policy.

Nothing in this repository's training or app code reads or writes files here
except:

- `evals/chat_eval_schema.py` -- defines the `EvalRecord` schema both sides
  agree on (`schema/chat_eval_record.schema.json` is the exported JSON Schema
  for non-Python tooling).
- `evals/chat_eval_validate.py` -- validates a `*.jsonl` file against that
  schema; run before asking for review.
- `evals/chat_overlap_check.py` -- reads this directory plus training output
  to report suspected train/eval overlap; it never edits files here.
- `reserved_seed_registry.jsonl` -- the one file the training side also reads,
  containing only `seed_id`/`content_hash` reservations, never transcripts or
  targets (see the guideline's leakage-prevention section).

## Layout

- `reserved_seed_registry.jsonl` -- shared coordination file (starts empty).
- `schema/chat_eval_record.schema.json` -- exported JSON Schema for `EvalRecord`.
- `templates/chat_eval_template.jsonl` -- 3 fictional example rows (one each for
  `complete`, `incomplete_one_followup`, `emergency`) showing the exact shape
  and field conventions. Copy from these, don't ship them as real eval cases.
- Real gold files (e.g. `seongnam_gold.jsonl`) are added by the eval-data
  owner following the guideline; none exist yet in this branch.

## Commands

```bash
python -m evals.chat_eval_validate --file eval_dataset/seongnam_gold.jsonl
python -m evals.chat_overlap_check \
    --training-file dataset/chat_training_pilot_v1/**/*.jsonl \
    --eval-file eval_dataset/seongnam_gold.jsonl
```
