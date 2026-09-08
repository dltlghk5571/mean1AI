import json

import pytest

from evals.chat_training_gen import SeedTemplate
from evals.chat_training_manual_import import import_run, main, write_import_result

_SEED = SeedTemplate(
    seed_template_id="l2-normal-001",
    region_scope="seongnam",
    category="road_damage",
    case_type="normal",
    scenario_brief="Citizen reports a pothole near a Seongnam intersection.",
)

_INCOMPLETE_SEED = SeedTemplate(
    seed_template_id="l2-incomplete-001",
    region_scope="seongnam",
    category="park_facility",
    case_type="incomplete",
    scenario_brief="Citizen complains about a broken swing but doesn't say which park.",
)

_EMERGENCY_SEED = SeedTemplate(
    seed_template_id="l2-emergency-001",
    region_scope="seongnam",
    category="safety",
    case_type="emergency",
    scenario_brief="Citizen reports a gas leak, urgent tone.",
)

_NORMAL_TARGET = {
    "assistant_message": "접수할 내용을 정리했습니다.",
    "title": "가로수 쓰러짐",
    "content": "분당구 정자동에서 가로수가 쓰러졌습니다.",
    "location_text": "분당구 정자동",
    "ready_to_submit": True,
}


def _envelope(seed_id: str, variants: list[dict]) -> dict:
    return {"seed_template_id": seed_id, "teacher_model": "gpt-5.1-manual", "variants": variants}


def _variant(variant_id: int, transcript: str, target: dict | None = None) -> dict:
    return {"variant_id": variant_id, "transcript": transcript, "target": target or _NORMAL_TARGET}


def _write_manifest(run_dir, seed_ids: list[str]) -> None:
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "test-run",
                "variants_per_seed": 2,
                "prompt_version": "test-prompt.v1",
                "generation_version": "test-gen.v1",
                "expected_seed_ids": seed_ids,
                "expected_variant_keys": [f"{sid}::v{v}" for sid in seed_ids for v in (1, 2)],
                "skipped_reserved_seed_ids": [],
            }
        ),
        encoding="utf-8",
    )


def _write_response(run_dir, seed_id: str, payload) -> None:
    responses_dir = run_dir / "manual_responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    (responses_dir / f"{seed_id}.json").write_text(text, encoding="utf-8")


def _two_variant_response(seed_id: str, tag: str = "") -> dict:
    return _envelope(
        seed_id,
        [
            _variant(1, f"신고합니다 첫번째 변형 {tag}".strip()),
            _variant(2, f"신고합니다 두번째 변형, 표현이 다릅니다 {tag}".strip()),
        ],
    )


def test_happy_path_two_variants_become_sft_candidate_drafts(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(
        run_dir,
        "l2-normal-001",
        _envelope(
            "l2-normal-001",
            [
                _variant(1, "가로수가 쓰러졌어요, 분당구 정자동입니다."),
                _variant(2, "정자동에 가로수가 넘어져서 신고합니다."),
            ],
        ),
    )

    records, content_rejected, completeness_issues, review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not content_rejected
    assert not completeness_issues
    assert len(records) == 2
    assert all(r.bucket == "sft_candidate" for r in records)
    assert all(r.provenance.review_status == "draft" for r in records)  # never auto-approved
    assert summary.accepted_draft_count == 2
    assert summary.excluded_safety_signal_count == 0
    assert summary.content_rejected_count == 0
    assert summary.present_response_file_count == 1
    assert summary.missing_response_file_count == 0
    assert summary.invalid_response_file_count == 0
    assert summary.present_variant_count == 2
    assert summary.missing_variant_count == 0
    assert summary.present_variant_keys == ["l2-normal-001::v1", "l2-normal-001::v2"]
    assert summary.missing_variant_keys == []
    assert len(review) == 2
    for entry in review:
        assert entry.reviewer_decision == "pending"
        assert entry.factual_faithfulness == "pending"
        assert entry.korean_naturalness == "pending"
        assert entry.one_question_at_a_time == "pending"
        assert entry.ready_to_submit_correctness == "pending"
        assert entry.hallucinated_detail_check == "pending"
        assert entry.pii_handling == "pending"


def test_safety_triggering_target_routes_to_excluded_safety_signal(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-emergency-001"])
    emergency_target = dict(_NORMAL_TARGET, content="지금 가스 냄새가 심하게 나서 대피 중입니다.")
    _write_response(
        run_dir,
        "l2-emergency-001",
        _envelope(
            "l2-emergency-001",
            [
                _variant(1, "가스 냄새가 나서 대피했어요.", emergency_target),
                _variant(2, "가스 누출 신고합니다, 대피 중입니다.", emergency_target),
            ],
        ),
    )

    records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-emergency-001": _EMERGENCY_SEED}
    )

    assert not content_rejected
    assert not completeness_issues
    assert len(records) == 2
    assert all(r.bucket == "excluded_safety_signal" for r in records)
    for record in records:
        assert record.safety_signal is not None
        assert "case_type_emergency" in record.safety_signal.triggers
    assert summary.excluded_safety_signal_count == 2
    assert summary.excluded_safety_signal_trigger_counts["case_type_emergency"] == 2
    # excluded_safety_signal is still a fully-present, fully-processed variant,
    # not a completeness gap or a content rejection.
    assert summary.content_rejected_count == 0
    assert summary.missing_variant_count == 0


def test_markdown_fence_is_a_completeness_issue_not_a_content_rejection(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(run_dir, "l2-normal-001", "```json\n{}\n```")

    _records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not content_rejected
    assert summary.content_rejected_count == 0
    assert completeness_issues[0].reason == "markdown_fence_detected"
    assert summary.invalid_response_file_count == 1
    assert summary.present_response_file_count == 1
    assert summary.invalid_variant_keys == []  # file-level, not variant-level
    assert summary.missing_variant_keys == ["l2-normal-001::v1", "l2-normal-001::v2"]


def test_invalid_json_is_a_completeness_issue(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(run_dir, "l2-normal-001", "{not valid json")

    _records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not content_rejected
    assert completeness_issues[0].reason == "invalid_json"
    assert summary.invalid_response_file_count == 1


def test_schema_violation_is_a_completeness_issue(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    # extra unexpected top-level field -> extra="forbid" on ManualResponseEnvelope
    payload = _envelope("l2-normal-001", [_variant(1, "t1"), _variant(2, "t2")])
    payload["unexpected_field"] = "boom"
    _write_response(run_dir, "l2-normal-001", payload)

    _records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not content_rejected
    assert completeness_issues[0].reason == "schema_invalid"
    assert summary.invalid_response_file_count == 1


def test_seed_id_mismatch_is_a_completeness_issue(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(
        run_dir,
        "l2-normal-001",
        _envelope("some-other-seed-id", [_variant(1, "t1"), _variant(2, "t2")]),
    )

    _records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not content_rejected
    assert completeness_issues[0].reason == "seed_id_mismatch"
    assert summary.invalid_response_file_count == 1


def test_unknown_seed_id_is_a_completeness_issue(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(run_dir, "l2-normal-001", _two_variant_response("l2-normal-001"))

    # seeds_by_id deliberately does not contain l2-normal-001 (e.g. wrong --seed-file)
    _records, content_rejected, completeness_issues, _review, summary = import_run(run_dir, {})

    assert not content_rejected
    assert completeness_issues[0].reason == "seed_not_found_in_seed_file"
    assert summary.invalid_response_file_count == 1
    assert summary.present_response_file_count == 1


def test_missing_variant_is_a_completeness_issue(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(run_dir, "l2-normal-001", _envelope("l2-normal-001", [_variant(1, "only one")]))

    records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not content_rejected
    reasons = [r.reason for r in completeness_issues]
    assert "missing_variant" in reasons
    assert len(records) == 1  # variant 1 still processed on its own
    assert "l2-normal-001::v2" in summary.missing_variant_keys
    assert summary.missing_variant_count == 1
    assert summary.present_variant_count == 1


def test_duplicate_variant_id_is_a_completeness_issue(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(
        run_dir,
        "l2-normal-001",
        _envelope(
            "l2-normal-001",
            [_variant(1, "first copy"), _variant(1, "second copy, same id")],
        ),
    )

    records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not content_rejected
    reasons = [r.reason for r in completeness_issues]
    assert "duplicate_variant" in reasons
    # both copies of variant_id=1 are skipped from processing (ambiguous which is "real")
    assert not records
    assert "l2-normal-001::v1" in summary.duplicated_variant_keys
    # an ambiguous/duplicated key is never "present" either, so it counts missing
    assert "l2-normal-001::v1" in summary.missing_variant_keys
    # variant 2 was never provided at all -> also reported missing
    assert "missing_variant" in reasons
    assert summary.missing_variant_count == 2


def test_unexpected_variant_id_is_a_completeness_issue(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(
        run_dir,
        "l2-normal-001",
        _envelope(
            "l2-normal-001",
            [_variant(1, "t1"), _variant(2, "t2"), _variant(3, "an extra unexpected variant")],
        ),
    )

    records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not content_rejected
    reasons = [r.reason for r in completeness_issues]
    assert "unexpected_variant" in reasons
    assert len(records) == 2
    assert "l2-normal-001::v3" in summary.invalid_variant_keys
    assert summary.missing_variant_count == 0  # v1 and v2 both still present


def test_missing_response_file_is_completeness_not_content_rejection(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    # no manual_responses/l2-normal-001.json written at all

    records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not records
    assert not content_rejected  # never counted as a rejected model generation
    assert summary.content_rejected_count == 0
    assert completeness_issues[0].reason == "response_file_missing"
    assert summary.missing_response_file_count == 1
    assert summary.present_response_file_count == 0
    assert summary.expected_response_file_count == 1
    assert summary.missing_variant_keys == ["l2-normal-001::v1", "l2-normal-001::v2"]
    assert summary.missing_variant_count == 2


def test_exact_duplicate_transcript_across_variants_is_a_content_rejection(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(
        run_dir,
        "l2-normal-001",
        _envelope(
            "l2-normal-001",
            [
                _variant(1, "완전히 동일한 민원 내용입니다."),
                _variant(2, "완전히 동일한 민원 내용입니다."),
            ],
        ),
    )

    records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not completeness_issues
    assert len(records) == 1
    assert content_rejected[0].reason == "duplicate_content_hash"
    assert summary.content_rejected_count == 1
    # both variants were present -- this is a content-quality rejection, not a gap
    assert summary.present_variant_count == 2
    assert summary.missing_variant_count == 0


def test_near_duplicate_transcript_across_variants_is_a_content_rejection(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(
        run_dir,
        "l2-normal-001",
        _envelope(
            "l2-normal-001",
            [
                _variant(
                    1,
                    "분당구 정자동에서 가로수가 쓰러져서 도로를 막고 있습니다 "
                    "확인 부탁드립니다 빠른 조치 부탁드립니다 감사합니다",
                ),
                _variant(
                    2,
                    "분당구 정자동에서 가로수가 쓰러져서 도로를 막고 있습니다 "
                    "확인 부탁드립니다 빠른 조치 부탁드립니다 감사합니다.",
                ),
            ],
        ),
    )

    records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not completeness_issues
    assert len(records) == 1
    assert content_rejected[0].reason == "near_duplicate_content"
    assert summary.content_rejected_count == 1


def test_pii_leak_in_target_is_a_content_rejection(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    leaking_target = dict(
        _NORMAL_TARGET, content="제 전화번호는 010-1234-5678이고 이름은 홍길동입니다."
    )
    _write_response(
        run_dir,
        "l2-normal-001",
        _envelope(
            "l2-normal-001",
            [_variant(1, "신고합니다 1", leaking_target), _variant(2, "신고합니다 2, 다른 표현")],
        ),
    )

    records, content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert not completeness_issues
    assert len(records) == 1
    assert content_rejected[0].reason == "pii_leak_in_target"
    assert summary.content_rejected_count == 1
    assert summary.present_variant_count == 2


def test_mixed_batch_summary_counts(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seed_ids = ["l2-normal-001", "l2-incomplete-001", "l2-emergency-001"]
    _write_manifest(run_dir, seed_ids)
    _write_response(
        run_dir,
        "l2-normal-001",
        _envelope(
            "l2-normal-001",
            [_variant(1, "정상 민원 하나"), _variant(2, "정상 민원 둘, 다른 내용")],
        ),
    )
    incomplete_target = dict(_NORMAL_TARGET, ready_to_submit=False)
    _write_response(
        run_dir,
        "l2-incomplete-001",
        _envelope(
            "l2-incomplete-001",
            [
                _variant(1, "그네가 고장났어요", incomplete_target),
                _variant(2, "놀이터 그네가 부서졌어요", incomplete_target),
            ],
        ),
    )
    # l2-emergency-001 has no response file -> counted as a completeness gap, not rejected

    seeds_by_id = {
        "l2-normal-001": _SEED,
        "l2-incomplete-001": _INCOMPLETE_SEED,
        "l2-emergency-001": _EMERGENCY_SEED,
    }
    records, content_rejected, completeness_issues, review, summary = import_run(
        run_dir, seeds_by_id
    )

    assert summary.accepted_draft_count == 4
    assert summary.excluded_safety_signal_count == 0
    assert summary.content_rejected_count == 0
    assert not content_rejected
    assert summary.expected_response_file_count == 3
    assert summary.present_response_file_count == 2
    assert summary.missing_response_file_count == 1
    assert summary.invalid_response_file_count == 0
    assert summary.expected_variant_count == 6
    assert summary.present_variant_count == 4
    assert summary.missing_variant_count == 2
    assert summary.bucket_counts == {"sft_candidate": 4}
    assert summary.case_type_counts == {"normal": 2, "incomplete": 2}
    assert len(records) == 4
    assert len(review) == 4
    assert any(i.reason == "response_file_missing" for i in completeness_issues)
    assert len(completeness_issues) == 1


def test_partial_import_leaves_existing_valid_responses_visible(tmp_path) -> None:
    """Requirement: existing valid responses remain visible when other
    response files are still missing -- a partial pilot batch must not hide
    or block the seeds that are already done."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seed_ids = ["l2-normal-001", "l2-incomplete-001"]
    _write_manifest(run_dir, seed_ids)
    _write_response(run_dir, "l2-normal-001", _two_variant_response("l2-normal-001"))
    # l2-incomplete-001 response file intentionally never written

    seeds_by_id = {"l2-normal-001": _SEED, "l2-incomplete-001": _INCOMPLETE_SEED}
    records, _content_rejected, completeness_issues, _review, summary = import_run(
        run_dir, seeds_by_id
    )

    assert len(records) == 2
    assert {r.provenance.seed_template_id for r in records} == {"l2-normal-001"}
    assert summary.present_response_file_count == 1
    assert summary.missing_response_file_count == 1
    assert "l2-normal-001::v1" in summary.present_variant_keys
    assert "l2-normal-001::v2" in summary.present_variant_keys
    assert "l2-incomplete-001::v1" in summary.missing_variant_keys
    assert completeness_issues[0].seed_template_id == "l2-incomplete-001"


def test_incremental_rerun_adds_new_seed_without_duplicating_existing(tmp_path) -> None:
    """Requirement: the importer can be rerun incrementally as more response
    files are added, without duplicating already-normalized records."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seed_ids = ["l2-normal-001", "l2-incomplete-001"]
    _write_manifest(run_dir, seed_ids)
    _write_response(run_dir, "l2-normal-001", _two_variant_response("l2-normal-001"))
    seeds_by_id = {"l2-normal-001": _SEED, "l2-incomplete-001": _INCOMPLETE_SEED}

    # First pass: only l2-normal-001 has a response file.
    records_1, rejected_1, issues_1, review_1, summary_1 = import_run(run_dir, seeds_by_id)
    write_import_result(run_dir, records_1, rejected_1, issues_1, review_1, summary_1)
    assert len(records_1) == 2
    assert summary_1.present_response_file_count == 1

    # Second pass: add the second seed's response file, rerun.
    _write_response(
        run_dir,
        "l2-incomplete-001",
        _envelope(
            "l2-incomplete-001",
            [
                _variant(1, "그네가 고장났어요", dict(_NORMAL_TARGET, ready_to_submit=False)),
                _variant(
                    2, "놀이터 그네가 부서졌어요", dict(_NORMAL_TARGET, ready_to_submit=False)
                ),
            ],
        ),
    )
    records_2, rejected_2, issues_2, review_2, summary_2 = import_run(run_dir, seeds_by_id)
    write_import_result(run_dir, records_2, rejected_2, issues_2, review_2, summary_2)

    assert len(records_2) == 4  # the original 2 plus the newly-added 2, never duplicated
    assert summary_2.present_response_file_count == 2
    assert summary_2.missing_response_file_count == 0

    normalized_lines = (
        (run_dir / "normalized" / "seongnam" / "sft_candidate.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(normalized_lines) == 4
    training_ids = [json.loads(line)["training_id"] for line in normalized_lines]
    assert len(training_ids) == len(set(training_ids))  # no duplicates
    assert set(training_ids) == {
        "l2-normal-001::v1",
        "l2-normal-001::v2",
        "l2-incomplete-001::v1",
        "l2-incomplete-001::v2",
    }


def test_rerunning_with_identical_inputs_is_byte_identical(tmp_path) -> None:
    """Requirement: rerunning with identical inputs produces identical
    normalized output and summary."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(run_dir, "l2-normal-001", _two_variant_response("l2-normal-001"))
    seeds_by_id = {"l2-normal-001": _SEED}

    records_1, rejected_1, issues_1, review_1, summary_1 = import_run(run_dir, seeds_by_id)
    write_import_result(run_dir, records_1, rejected_1, issues_1, review_1, summary_1)
    normalized_path = run_dir / "normalized" / "seongnam" / "sft_candidate.jsonl"
    first_bytes = normalized_path.read_bytes()
    first_summary_bytes = (run_dir / "summary.json").read_bytes()

    records_2, rejected_2, issues_2, review_2, summary_2 = import_run(run_dir, seeds_by_id)
    write_import_result(run_dir, records_2, rejected_2, issues_2, review_2, summary_2)
    second_bytes = normalized_path.read_bytes()
    second_summary_bytes = (run_dir / "summary.json").read_bytes()

    assert first_bytes == second_bytes
    assert first_summary_bytes == second_summary_bytes


def test_write_import_result_produces_expected_file_layout(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(
        run_dir,
        "l2-normal-001",
        _envelope(
            "l2-normal-001",
            [_variant(1, "가로수 쓰러짐 신고 1"), _variant(2, "가로수 쓰러짐 신고 2, 다른 표현")],
        ),
    )
    records, content_rejected, completeness_issues, review, summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )
    write_import_result(run_dir, records, content_rejected, completeness_issues, review, summary)

    assert (run_dir / "normalized" / "seongnam" / "sft_candidate.jsonl").exists()
    assert (run_dir / "rejected" / "content_rejected.jsonl").exists()
    assert (run_dir / "rejected" / "completeness_issues.jsonl").exists()
    assert (run_dir / "review" / "review.jsonl").exists()
    assert (run_dir / "summary.json").exists()

    lines = (
        (run_dir / "normalized" / "seongnam" / "sft_candidate.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(lines) == 2
    for line in lines:
        record = json.loads(line)
        assert record["provenance"]["review_status"] == "draft"  # never auto-approved

    review_lines = (run_dir / "review" / "review.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(review_lines) == 2
    for line in review_lines:
        entry = json.loads(line)
        assert entry["reviewer_decision"] == "pending"

    assert (run_dir / "rejected" / "content_rejected.jsonl").read_text(encoding="utf-8") == ""
    assert (run_dir / "rejected" / "completeness_issues.jsonl").read_text(encoding="utf-8") == ""


def test_declared_teacher_model_is_caller_supplied_not_hardcoded(tmp_path) -> None:
    """The teacher_model recorded in provenance must come from whatever the
    human typed into the pasted envelope -- never a hardcoded model name."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    envelope = _two_variant_response("l2-normal-001")
    envelope["teacher_model"] = "some totally different declared model string"
    _write_response(run_dir, "l2-normal-001", envelope)

    records, _content_rejected, _completeness_issues, _review, _summary = import_run(
        run_dir, {"l2-normal-001": _SEED}
    )

    assert all(
        r.provenance.teacher_model == "some totally different declared model string"
        for r in records
    )


def test_cli_reports_missing_manifest_as_exit_code_2(tmp_path) -> None:
    seed_file = tmp_path / "seeds.jsonl"
    seed_file.write_text(_SEED.model_dump_json() + "\n", encoding="utf-8")
    exit_code = main(
        ["--run-dir", str(tmp_path / "no-manifest-here"), "--seed-file", str(seed_file)]
    )
    assert exit_code == 2


def test_cli_reports_bad_seed_file_as_exit_code_2(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    exit_code = main(
        ["--run-dir", str(run_dir), "--seed-file", str(tmp_path / "does-not-exist.jsonl")]
    )
    assert exit_code == 2


def test_cli_end_to_end_writes_all_outputs(tmp_path) -> None:
    seed_file = tmp_path / "seeds.jsonl"
    seed_file.write_text(_SEED.model_dump_json() + "\n", encoding="utf-8")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_manifest(run_dir, ["l2-normal-001"])
    _write_response(
        run_dir,
        "l2-normal-001",
        _envelope(
            "l2-normal-001",
            [_variant(1, "가로수 쓰러짐 신고 1"), _variant(2, "가로수 쓰러짐 신고 2, 다른 표현")],
        ),
    )

    exit_code = main(["--run-dir", str(run_dir), "--seed-file", str(seed_file)])
    assert exit_code == 0
    assert (run_dir / "summary.json").exists()
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["accepted_draft_count"] == 2
    assert summary["content_rejected_count"] == 0
    assert "rejected_count" not in summary  # replaced by the split counts


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
