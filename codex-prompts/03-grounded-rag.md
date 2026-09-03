# Codex task: grounded RAG v1

Goal: replace the template-only draft path with a citation-enforced draft generator while preserving an
offline deterministic provider for tests.

Requirements:
- Add document effective date, version, approval status, and superseded-by metadata.
- Retrieve only approved, effective documents.
- The model output must be structured and list source IDs supporting each substantive sentence.
- Reject or flag any sentence without a valid source mapping.
- Show citations to the reviewing officer; never auto-send.
- Add tests for stale, conflicting, malicious, and irrelevant documents.

Do not add embeddings until a lexical baseline and eval comparison exist. Run all checks and document the
measured trade-off.
