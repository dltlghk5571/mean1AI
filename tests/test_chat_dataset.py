import json

from evals.chat_dataset import AuditDraftRow, build_dataset

SEED = 20260907


def _log_line(
    *,
    draft_id: str | None,
    transcript: str = "사용자: 가로등이 꺼졌어요",
    title: str = "가로등 민원",
    content: str = "산책로 가로등이 꺼져 있습니다.",
    location_text: str = "데모공원",
    ready_to_submit: bool = True,
    safety_overridden: bool = False,
    final_ready: bool | None = None,
) -> str:
    payload = {
        "draft_id": draft_id,
        "transcript": transcript,
        "model_output": {
            "assistant_message": "확인했습니다.",
            "title": title,
            "content": content,
            "location_text": location_text,
            "ready_to_submit": ready_to_submit,
        },
        "safety_overridden": safety_overridden,
        "final_ready": final_ready if final_ready is not None else ready_to_submit,
    }
    body = json.dumps(payload, ensure_ascii=False)
    return f"2026-09-07 10:00:00,000 INFO app.chat_eval chat_turn_extraction {body}"


def test_unrelated_lines_are_skipped_not_rejected() -> None:
    lines = ["2026-09-07 10:00:00,000 INFO app.other some_unrelated_message {}"]
    result = build_dataset(lines, [], seed=SEED)
    assert result.records == []
    assert result.rejected == []
    assert result.summary.log_lines_matched == 0


def test_malformed_json_is_rejected() -> None:
    lines = ["... app.chat_eval chat_turn_extraction {not-json"]
    result = build_dataset(lines, [], seed=SEED)
    assert [r.reason for r in result.rejected] == ["malformed_json"]
    assert result.summary.log_lines_matched == 1


def test_schema_invalid_is_rejected() -> None:
    lines = ["... app.chat_eval chat_turn_extraction " + json.dumps({"draft_id": None})]
    result = build_dataset(lines, [], seed=SEED)
    assert [r.reason for r in result.rejected] == ["schema_invalid"]


def test_duplicate_draft_id_in_logs_excluded_and_reported() -> None:
    draft_id = "11111111-1111-1111-1111-111111111111"
    lines = [_log_line(draft_id=draft_id), _log_line(draft_id=draft_id)]
    result = build_dataset(lines, [], seed=SEED)
    assert result.records == []
    assert [r.reason for r in result.rejected] == ["duplicate_draft_id_log"]


def test_duplicate_draft_id_in_audit_excluded_and_reported() -> None:
    draft_id = "22222222-2222-2222-2222-222222222222"
    lines = [_log_line(draft_id=draft_id)]
    audit = [
        AuditDraftRow(complaint_id="c1", draft_id=draft_id, edited=False),
        AuditDraftRow(complaint_id="c2", draft_id=draft_id, edited=True),
    ]
    result = build_dataset(lines, audit, seed=SEED)
    assert "duplicate_draft_id_audit" in [r.reason for r in result.rejected]
    # No accepted_* record should be produced from an ambiguous audit join.
    assert result.summary.category_counts.get("accepted_unedited", 0) == 0
    assert result.summary.category_counts.get("accepted_edited", 0) == 0
    # The draft itself still surfaces as unsubmitted since the audit side was thrown out.
    assert result.summary.category_counts.get("unsubmitted", 0) == 1


def test_missing_eval_log_for_audit_is_reported() -> None:
    draft_id = "33333333-3333-3333-3333-333333333333"
    audit = [AuditDraftRow(complaint_id="c1", draft_id=draft_id, edited=False)]
    result = build_dataset([], audit, seed=SEED)
    assert result.records == []
    assert [r.reason for r in result.rejected] == ["missing_eval_log_for_audit"]


def test_categorization_accepted_unedited_edited_unsubmitted_safety_eval() -> None:
    unedited_id = "44444444-4444-4444-4444-444444444444"
    edited_id = "55555555-5555-5555-5555-555555555555"
    unsubmitted_id = "66666666-6666-6666-6666-666666666666"
    lines = [
        _log_line(draft_id=unedited_id),
        _log_line(draft_id=edited_id),
        _log_line(draft_id=unsubmitted_id),
        _log_line(draft_id=None, ready_to_submit=False, final_ready=False),
        _log_line(draft_id=None, safety_overridden=True, ready_to_submit=True, final_ready=False),
    ]
    audit = [
        AuditDraftRow(complaint_id="c1", draft_id=unedited_id, edited=False),
        AuditDraftRow(complaint_id="c2", draft_id=edited_id, edited=True),
    ]
    result = build_dataset(lines, audit, seed=SEED)
    by_category = {(r.category, r.draft_id) for r in result.records}
    assert (("accepted_unedited", unedited_id)) in by_category
    assert (("accepted_edited", edited_id)) in by_category
    assert (("unsubmitted", unsubmitted_id)) in by_category
    assert result.summary.category_counts["safety_eval"] == 2
    # safety_eval rows never carry a train/val/test split assignment.
    assert all(r.split is None for r in result.records if r.category == "safety_eval")
    # positive SFT categories always get a split assignment.
    assert all(r.split is not None for r in result.records if r.category != "safety_eval")


def test_target_and_messages_are_model_ready() -> None:
    draft_id = "77777777-7777-7777-7777-777777777777"
    lines = [_log_line(draft_id=draft_id, transcript="사용자: 테스트")]
    result = build_dataset(lines, [], seed=SEED)
    [record] = result.records
    assert record.messages[0]["role"] == "system"
    assert record.messages[1] == {"role": "user", "content": "사용자: 테스트"}
    assert record.target.title == "가로등 민원"
    assert record.target.ready_to_submit is True


def test_split_assignment_is_deterministic_across_runs() -> None:
    draft_id = "88888888-8888-8888-8888-888888888888"
    lines = [_log_line(draft_id=draft_id)]
    audit = [AuditDraftRow(complaint_id="c1", draft_id=draft_id, edited=False)]
    first = build_dataset(lines, audit, seed=SEED)
    second = build_dataset(lines, audit, seed=SEED)
    assert first.records[0].split == second.records[0].split

    different_seed = build_dataset(lines, audit, seed=SEED + 1)
    # Not asserting inequality (a different seed can coincidentally land on the
    # same bucket); asserting determinism per seed is the actual contract.
    assert different_seed.records[0].split in ("train", "val", "test")


def test_same_complaint_id_never_crosses_splits() -> None:
    # Two accepted drafts sharing a complaint_id (defensive: shouldn't happen
    # in practice, but the grouping key must still hold if it ever does).
    id_a, id_b = (
        "99999999-9999-9999-9999-999999999991",
        "99999999-9999-9999-9999-999999999992",
    )
    lines = [_log_line(draft_id=id_a), _log_line(draft_id=id_b)]
    audit = [
        AuditDraftRow(complaint_id="shared", draft_id=id_a, edited=False),
        AuditDraftRow(complaint_id="shared", draft_id=id_b, edited=True),
    ]
    result = build_dataset(lines, audit, seed=SEED)
    splits = {r.split for r in result.records}
    assert len(splits) == 1


def test_summary_field_length_stats_are_computed() -> None:
    draft_id = "10101010-1010-1010-1010-101010101010"
    lines = [_log_line(draft_id=draft_id, title="가로등 민원", content="내용입니다")]
    result = build_dataset(lines, [], seed=SEED)
    stats = result.summary.field_length_by_category["unsubmitted"]["title"]
    assert stats.count == 1
    assert stats.min == stats.max == stats.mean == len("가로등 민원")
