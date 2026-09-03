# Codex task: location confirmation and duplicate candidates

Goal: add a safe, local-only duplicate-candidate scorer using complaint category, normalized location,
time window, and text similarity.

Constraints:
- Never merge or close complaints automatically.
- Return candidates with evidence and scores for human confirmation.
- Do not introduce an external map API yet.
- Add adversarial tests for same wording at different places and different wording at the same place.

Done when the API exposes duplicate candidates, the UI explains why each was suggested, audit events
record confirmation/rejection, and all checks pass.
