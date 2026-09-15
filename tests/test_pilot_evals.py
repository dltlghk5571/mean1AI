import json
from pathlib import Path

import pytest

from evals.pilot_evaluator import (
    DEFAULT_PILOT_FIXTURE,
    export_rows,
    load_cases,
    load_rows,
    score,
    source_review,
    validate_dataset,
)
from evals.pilot_models import PilotCase, PilotReport, Prediction, Split
from evals.pilot_run import contract, main


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), "utf-8")
    return path


def test_pilot_has_twelve_topics_and_separate_synthetic_families() -> None:
    result = validate_dataset()
    assert result["cases"] == 100
    assert result["families"] == 50
    assert result["split_counts"] == {"train": 24, "dev": 24, "test": 52}
    assert result["model_evaluated"] is False
    assert result["review_status"] == "draft"
    service_rows = contract()["services"]
    assert isinstance(service_rows, list) and len(service_rows) == 12


@pytest.mark.parametrize("split", ["dev", "test"])
def test_model_export_omits_gold_metadata_and_refuses_heldout_labels(split: Split) -> None:
    cases = load_cases()
    rows = export_rows(cases, split)
    assert rows
    assert all(set(row) == {"dataset_version", "id", "input"} for row in rows)
    assert all(
        set(row["input"]) == {"messages", "known_fields", "skipped_fields", "submission_confirmed"}
        for row in rows
    )
    with pytest.raises(ValueError, match="Only the train"):
        export_rows(cases, split, with_labels=True)
    train = export_rows(cases, "train", with_labels=True)
    assert len(train) == 24
    assert all("expected" in row and row["review_status"] == "draft" for row in train)


@pytest.mark.parametrize("problem", ["family", "input", "utterance", "id"])
def test_dataset_detects_leakage_and_duplicate_ids(tmp_path: Path, problem: str) -> None:
    rows = [c.model_dump(mode="json") for c in load_cases()[:2]]
    if problem == "family":
        rows[1]["split"] = "dev"
    elif problem == "input":
        rows[1]["input"] = rows[0]["input"]
    elif problem == "utterance":
        rows[1]["family_id"] = "pf999"
        rows[1]["split"] = "dev"
        rows[1]["input"]["messages"] = [
            {"role": "assistant", "content": "알려 주세요."},
            *rows[0]["input"]["messages"],
        ]
    else:
        rows[1]["id"] = rows[0]["id"]
    with pytest.raises(ValueError, match="crosses splits|Duplicate"):
        load_cases(write_jsonl(tmp_path / "bad.jsonl", rows))


@pytest.mark.parametrize("problem", ["field", "service", "ungrounded", "repeated", "urgent"])
def test_dataset_refuses_invalid_gold(tmp_path: Path, problem: str) -> None:
    row = load_cases()[0].model_dump(mode="json")
    if problem == "field":
        row["expected"]["question_fields"] = ["resident_number"]
    elif problem == "service":
        row["expected"]["service_id"] = "invented-department"
    elif problem == "ungrounded":
        row["expected"]["extracted_fields"] = {"location_text": ["지어낸 장소"]}
    elif problem == "repeated":
        row["expected"]["question_fields"] = ["facility_type"]
    else:
        row["expected"]["urgent"] = True
    with pytest.raises(ValueError):
        load_cases(write_jsonl(tmp_path / "bad.jsonl", [row]))


def unit_prediction(case: PilotCase) -> dict:
    """Scorer unit-test input only; never written as a claimed model evaluation result."""
    return {
        "dataset_version": case.dataset_version,
        "id": case.id,
        "service_id": case.expected.service_id,
        "intent": case.expected.intent,
        "urgent": case.expected.urgent,
        "needs_human_review": True,
        "department_id": None,
        "extracted_fields": {k: values[0] for k, values in case.expected.extracted_fields.items()},
        "next_action": case.expected.next_actions[0],
        "questions": case.expected.question_fields[:1]
        if case.expected.next_actions[0] == "ask"
        else [],
    }


def unit_score(tmp_path: Path, cases: list[PilotCase], predictions: list[dict]) -> PilotReport:
    fixture = write_jsonl(tmp_path / "cases.jsonl", [c.model_dump(mode="json") for c in cases])
    outputs = write_jsonl(tmp_path / "predictions.jsonl", predictions)
    return score(outputs, cases[0].split, "unit-test-only", fixture)


def test_score_matches_ids_not_order_and_reports_hand_calculated_f1(tmp_path: Path) -> None:
    all_cases = load_cases()
    cases = [all_cases[0], all_cases[1], all_cases[6]]  # Two road, one lighting; all train.
    predictions = [unit_prediction(c) for c in cases]
    predictions[1]["service_id"] = "pilot-service-lighting"
    report = unit_score(tmp_path, cases, list(reversed(predictions)))
    assert report.metrics["service_id"].value == pytest.approx(2 / 3)
    assert report.service_macro_f1 == pytest.approx(2 / 3)
    assert report.metrics["urgent_recall"].value is None
    assert report.service_metrics["pilot-service-parking"]["f1"] is None
    assert report.model_quality_certified is False
    assert report.all_cases_match is False
    assert len(report.dataset_sha256) == len(report.predictions_sha256) == 64


@pytest.mark.parametrize("problem", ["missing", "extra", "duplicate"])
def test_score_requires_exact_prediction_coverage(tmp_path: Path, problem: str) -> None:
    cases = load_cases()[:2]
    predictions = [unit_prediction(c) for c in cases]
    if problem == "missing":
        predictions.pop()
    elif problem == "extra":
        predictions.append({**predictions[0], "id": "pc999"})
    else:
        predictions.append(predictions[0])
    with pytest.raises(ValueError, match="exactly match|Duplicate prediction"):
        unit_score(tmp_path, cases, predictions)


def test_schema_does_not_coerce_booleans_and_errors_do_not_echo_text(
    tmp_path: Path, capsys
) -> None:
    prediction = unit_prediction(load_cases()[0])
    prediction["urgent"] = "PRIVATE-TEST-TOKEN"
    path = write_jsonl(tmp_path / "bad.jsonl", [prediction])
    assert (
        main(["score", "--split", "train", "--predictions", str(path), "--model-run", "test"]) == 2
    )
    captured = capsys.readouterr()
    assert "Invalid Prediction at line 1" in captured.err
    assert "PRIVATE-TEST-TOKEN" not in captured.err
    assert captured.out == ""


def test_score_catches_urgent_delay_invented_evidence_and_submission(tmp_path: Path) -> None:
    case = next(c for c in load_cases() if c.expected.urgent)
    prediction = unit_prediction(case)
    prediction.update(
        urgent=False,
        needs_human_review=False,
        department_id="invented-office",
        next_action="submit",
        questions=["location_text", "document_photo"],
        extracted_fields={"location_text": "확인하지 않은 장소"},
    )
    report = unit_score(tmp_path, [case], [prediction])
    for key in (
        "urgent_delayed",
        "human_review_bypassed",
        "unreviewed_department",
        "unsafe_action",
        "unconfirmed_submission",
        "multiple_questions",
        "unknown_or_private_field",
        "ungrounded_extraction",
    ):
        assert report.safety_violations[key] == 1
    assert report.metrics["urgent_recall"].value == 0.0
    assert report.metrics["extraction_precision"].value == 0.0
    assert report.metrics["extraction_recall"].value == 0.0


@pytest.mark.parametrize("field", ["location_text", "facility_type"])
def test_answered_and_skipped_fields_cannot_be_reasked(tmp_path: Path, field: str) -> None:
    case = next(c for c in load_cases() if "location_text" in c.input.skipped_fields)
    prediction = unit_prediction(case)
    prediction.update(next_action="ask", questions=[field])
    report = unit_score(tmp_path, [case], [prediction])
    assert report.safety_violations["repeated_question"] == 1
    assert report.metrics["next_step"].value == 0.0


def test_assistant_text_is_not_extraction_evidence(tmp_path: Path) -> None:
    cases = load_cases()
    case = next(
        c
        for c in cases
        if "assistant_not_evidence" in c.tags and "도로 작업" not in c.input.messages[-1].content
    )
    prediction = unit_prediction(case)
    prediction["extracted_fields"]["noise_source"] = "도로 작업"
    report = unit_score(tmp_path, [case], [prediction])
    assert report.safety_violations["ungrounded_extraction"] == 1
    assert "extracted_fields" in report.failures[0].checks


def test_abstention_and_unknown_service_are_distinct_failures(tmp_path: Path) -> None:
    case = next(c for c in load_cases() if c.expected.service_id is None)
    prediction = unit_prediction(case)
    prediction["service_id"] = "pilot-service-nonexistent"
    report = unit_score(tmp_path, [case], [prediction])
    assert report.metrics["abstention_recall"].value == 0.0
    assert report.safety_violations["unknown_service"] == 1


@pytest.mark.parametrize("action", ["decide_eligibility", "reject", "close", "merge_complaints"])
def test_high_impact_and_complaint_decisions_are_never_safe_actions(
    tmp_path: Path, action: str
) -> None:
    case = next(c for c in load_cases() if "welfare_decision" in c.tags)
    prediction = unit_prediction(case)
    prediction["next_action"] = action
    report = unit_score(tmp_path, [case], [prediction])
    assert report.safety_violations["unsafe_action"] == 1


def test_source_review_is_local_and_preserves_unresolved_mappings() -> None:
    inventory = source_review()
    assert inventory["live_verification_performed"] is False
    documents = inventory["documents"]
    services = inventory["services"]
    assert isinstance(documents, list) and len(documents) == 12
    assert isinstance(services, list)
    assert all("training_use_unapproved" in row["issues"] for row in documents)
    unresolved = {row["id"] for row in services if "work_assignment_unresolved" in row["issues"]}
    assert unresolved == {"pilot-service-basic-pension", "pilot-service-activity-support"}


def test_cli_writes_utf8_and_refuses_to_replace_existing_files(tmp_path: Path, capsys) -> None:
    path = tmp_path / "inputs.jsonl"
    assert main(["export", "--split", "test", "--output", str(path)]) == 0
    original = path.read_bytes()
    assert len(original.decode("utf-8").splitlines()) == 52
    assert main(["export", "--split", "train", "--output", str(path)]) == 2
    assert path.read_bytes() == original
    assert "input error" in capsys.readouterr().err


def test_cli_score_failure_is_one_and_validation_never_claims_model_quality(
    tmp_path: Path, capsys
) -> None:
    case = load_cases()[0]
    fixture = write_jsonl(tmp_path / "case.jsonl", [case.model_dump(mode="json")])
    output = unit_prediction(case)
    output["service_id"] = None
    path = write_jsonl(tmp_path / "prediction.jsonl", [output])
    assert (
        main(
            [
                "score",
                "--split",
                "train",
                "--fixture",
                str(fixture),
                "--predictions",
                str(path),
                "--model-run",
                "unit-test-only",
            ]
        )
        == 1
    )
    report = json.loads(capsys.readouterr().out)
    assert report["all_cases_match"] is False
    assert main(["validate", "--fixture", str(DEFAULT_PILOT_FIXTURE)]) == 0
    assert json.loads(capsys.readouterr().out)["model_evaluated"] is False


def test_strict_prediction_contract_rejects_extra_fields(tmp_path: Path) -> None:
    output = unit_prediction(load_cases()[0])
    output["unexpected_tool_call"] = "submit"
    with pytest.raises(ValueError, match="Invalid Prediction"):
        load_rows(write_jsonl(tmp_path / "extra.jsonl", [output]), Prediction)


def test_duplicate_json_keys_are_not_silently_overwritten(tmp_path: Path) -> None:
    output = json.dumps(unit_prediction(load_cases()[0]))
    output = output.replace('"urgent": false', '"urgent": true, "urgent": false')
    path = tmp_path / "duplicate-key.jsonl"
    path.write_text(output, "utf-8")
    with pytest.raises(ValueError, match="Invalid Prediction at line 1"):
        load_rows(path, Prediction)


def test_scorer_accepts_allowed_alternatives_without_claiming_model_performance(
    tmp_path: Path,
) -> None:
    cases = [c for c in load_cases() if c.split == "test" and "stale_policy" in c.tags]
    predictions = [unit_prediction(c) for c in cases]
    predictions[0]["next_action"] = "handoff"
    report = unit_score(tmp_path, cases, predictions)
    assert report.all_cases_match is True
    assert report.failures == []
    assert all(value == 0 for value in report.safety_violations.values())
    assert report.model_run == "unit-test-only"
    assert report.model_quality_certified is False
