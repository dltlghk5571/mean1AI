import json
import re
from collections import Counter
from pathlib import Path

import pytest

from evals.evaluator import (
    MINIMUM_DATASET_CASES,
    build_gate_failures,
    evaluate,
    ranked_routing_hits,
)
from evals.loader import load_routing_thresholds, load_suite
from evals.models import (
    DATASET_VERSION,
    CaseFailure,
    EvaluationReport,
    GateFailure,
    RatioMetric,
    RoutingCategoryMetrics,
)
from evals.run import format_markdown, main


@pytest.fixture(scope="module")
def evaluation_report() -> EvaluationReport:
    return evaluate()


def test_fixture_dataset_is_versioned_unique_and_synthetic() -> None:
    suite = load_suite()
    all_cases = [*suite.routing, *suite.urgency, *suite.pii, *suite.abstention]

    assert suite.total_cases >= MINIMUM_DATASET_CASES
    assert len({case.id for case in all_cases}) == suite.total_cases
    assert all(case.dataset_version == DATASET_VERSION for case in all_cases)
    assert all(case.synthetic is True for case in all_cases)


def test_v1_fixture_coverage_cannot_silently_shrink() -> None:
    suite = load_suite()

    assert len(suite.routing) == 96
    assert len(suite.urgency) == 48
    assert len(suite.pii) == 48
    assert len(suite.abstention) == 36
    assert sum(case.expected_urgency.value != "normal" for case in suite.urgency) == 38
    assert sum(case.sensitive for case in suite.abstention) == 28
    assert Counter(target.pii_type for case in suite.pii for target in case.targets) == Counter(
        {
            "resident_registration_number": 12,
            "email": 12,
            "mobile_phone": 12,
            "landline_phone": 12,
        }
    )


def test_routing_thresholds_are_versioned_and_cover_each_category() -> None:
    suite = load_suite()
    thresholds = load_routing_thresholds()

    assert thresholds.dataset_version == DATASET_VERSION
    assert thresholds.thresholds_version == "2026-09-04.v1"
    assert set(thresholds.categories) == {case.expected_category for case in suite.routing}
    assert all(threshold.minimum_cases >= 10 for threshold in thresholds.categories.values())


def test_evaluation_rejects_threshold_category_mismatch(tmp_path: Path) -> None:
    thresholds = load_routing_thresholds().model_dump(mode="json")
    del thresholds["categories"]["park"]
    incomplete_path = tmp_path / "incomplete-thresholds.json"
    incomplete_path.write_text(json.dumps(thresholds), encoding="utf-8")

    with pytest.raises(ValueError, match="must exactly match the dataset"):
        evaluate(thresholds_path=incomplete_path)


def test_pii_fixture_tokens_follow_synthetic_conventions() -> None:
    suite = load_suite()

    for case in suite.pii:
        for target in case.targets:
            compact = re.sub(r"\s|-", "", target.value)
            if target.pii_type == "resident_registration_number":
                assert compact[:6] in {"000000", "990000", "991332", "991399"}
            elif target.pii_type in {"mobile_phone", "landline_phone"}:
                assert "000" in compact
            elif target.pii_type == "email":
                assert target.value.endswith(("@example.com", "@example.org", "@example.net"))


def test_ranked_routing_hits_distinguish_top1_and_top3() -> None:
    top1_hit, top3_hit = ranked_routing_hits("EXPECTED", ["FIRST", "SECOND", "EXPECTED"])

    assert top1_hit is False
    assert top3_hit is True


def test_full_evaluation_meets_every_release_gate(evaluation_report: EvaluationReport) -> None:
    report = evaluation_report

    assert report.total_cases >= MINIMUM_DATASET_CASES
    assert report.provider == "rules"
    assert report.metrics.routing_top1_accuracy.value == 1.0
    assert report.metrics.routing_top3_accuracy.value == 1.0
    assert report.thresholds_version == "2026-09-04.v1"
    assert report.routing_thresholds.dataset_version == report.dataset_version
    matrix = report.metrics.routing_confusion_matrix
    assert matrix.total_cases == 96
    assert sum(sum(row.values()) for row in matrix.rows.values()) == 96
    for category in matrix.labels:
        assert matrix.rows[category][category] == 12
        assert (
            sum(
                count for predicted, count in matrix.rows[category].items() if predicted != category
            )
            == 0
        )
    assert report.metrics.emergency_recall.value == 1.0
    assert report.metrics.emergency_false_positive_rate.value == 0.0
    assert report.metrics.pii_masking_recall.value == 1.0
    assert all(metric.value == 1.0 for metric in report.metrics.pii_masking_recall_by_type.values())
    assert report.metrics.abstention_rate.value == 1.0
    assert report.metrics.sensitive_auto_assigned_count == 0
    assert report.metrics.sensitive_auto_finalized_count == 0
    assert report.metrics.urgent_auto_assigned_count == 0
    assert report.metrics.urgent_not_reviewed_count == 0
    assert report.metrics.unaudited_processing_count == 0
    assert report.failures == []
    assert report.gate_failures == []
    assert report.passed is True


@pytest.mark.parametrize(
    ("field", "gate"),
    [
        ("sensitive_auto_assigned_count", "sensitive_auto_assigned_count"),
        ("sensitive_auto_finalized_count", "sensitive_auto_finalized_count"),
        ("urgent_auto_assigned_count", "urgent_auto_assigned_count"),
        ("urgent_not_reviewed_count", "urgent_not_reviewed_count"),
        ("unaudited_processing_count", "unaudited_processing_count"),
    ],
)
def test_zero_tolerance_safety_gates_reject_any_violation(
    field: str, gate: str, evaluation_report: EvaluationReport
) -> None:
    report = evaluation_report
    regressed_metrics = report.metrics.model_copy(update={field: 1})

    failures = build_gate_failures(regressed_metrics, report.total_cases)

    assert gate in {failure.gate for failure in failures}


@pytest.mark.parametrize(
    ("field", "gate"),
    [
        ("emergency_recall", "emergency_recall"),
        ("pii_masking_recall", "pii_masking_recall"),
        ("abstention_rate", "abstention_rate"),
    ],
)
def test_perfect_recall_safety_gates_reject_a_single_miss(
    field: str, gate: str, evaluation_report: EvaluationReport
) -> None:
    report = evaluation_report
    missed_one = RatioMetric.from_counts(47, 48)
    regressed_metrics = report.metrics.model_copy(update={field: missed_one})

    failures = build_gate_failures(regressed_metrics, report.total_cases)

    assert gate in {failure.gate for failure in failures}


def test_case_level_safety_failure_is_a_release_gate(
    evaluation_report: EvaluationReport,
) -> None:
    case_failure = CaseFailure(
        suite="pii",
        case_id="pii-synthetic-regression",
        check="mask_email",
        expected="token removed and type detected",
        actual="removed=False, type_detected=False",
    )

    failures = build_gate_failures(
        evaluation_report.metrics,
        evaluation_report.total_cases,
        [case_failure],
    )

    assert "case_level_safety_failures" in {failure.gate for failure in failures}


def test_per_category_accuracy_gate_catches_regression_hidden_by_overall_score(
    evaluation_report: EvaluationReport,
) -> None:
    category = "road_damage"
    regressed_by_category = dict(evaluation_report.metrics.routing_by_category)
    regressed_by_category[category] = RoutingCategoryMetrics(
        cases=12,
        top1_accuracy=RatioMetric.from_counts(10, 12),
        top3_accuracy=RatioMetric.from_counts(12, 12),
    )
    regressed_metrics = evaluation_report.metrics.model_copy(
        update={"routing_by_category": regressed_by_category}
    )

    failures = build_gate_failures(
        regressed_metrics,
        evaluation_report.total_cases,
        routing_thresholds=evaluation_report.routing_thresholds,
    )

    assert f"routing_{category}_top1_accuracy" in {failure.gate for failure in failures}
    assert "routing_top1_accuracy" not in {failure.gate for failure in failures}


def test_per_category_minimum_case_gate_prevents_silent_coverage_shrink(
    evaluation_report: EvaluationReport,
) -> None:
    category = "park"
    regressed_by_category = dict(evaluation_report.metrics.routing_by_category)
    regressed_by_category[category] = RoutingCategoryMetrics(
        cases=9,
        top1_accuracy=RatioMetric.from_counts(9, 9),
        top3_accuracy=RatioMetric.from_counts(9, 9),
    )
    regressed_metrics = evaluation_report.metrics.model_copy(
        update={"routing_by_category": regressed_by_category}
    )

    failures = build_gate_failures(
        regressed_metrics,
        evaluation_report.total_cases,
        routing_thresholds=evaluation_report.routing_thresholds,
    )

    assert f"routing_{category}_minimum_cases" in {failure.gate for failure in failures}


def test_markdown_report_contains_category_gates_and_confusion_matrix(
    evaluation_report: EvaluationReport,
) -> None:
    markdown = format_markdown(evaluation_report)

    assert "# Offline evaluation report" in markdown
    assert "## Routing category gates" in markdown
    assert "## Routing confusion matrix" in markdown
    assert "Rows are expected categories; columns are predicted categories." in markdown
    assert "`road_damage`" in markdown
    assert "| Emergency recall | 100.0% |" in markdown


def test_cli_returns_nonzero_for_a_failed_gate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evaluation_report: EvaluationReport,
) -> None:
    failed_report = evaluation_report.model_copy(
        update={
            "gate_failures": [GateFailure(gate="synthetic_test_gate", expected="0", actual="1")],
            "passed": False,
        }
    )
    monkeypatch.setattr("evals.run.evaluate", lambda *_: failed_report)

    assert main([]) == 1
    assert '"passed": false' in capsys.readouterr().out


def test_cli_can_render_markdown_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evaluation_report: EvaluationReport,
) -> None:
    monkeypatch.setattr("evals.run.evaluate", lambda *_: evaluation_report)

    assert main(["--format", "markdown"]) == 0
    output = capsys.readouterr().out
    assert "## Routing category gates" in output
    assert "## Routing confusion matrix" in output
