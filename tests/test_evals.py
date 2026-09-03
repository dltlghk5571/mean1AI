import re
from collections import Counter

import pytest

from evals.evaluator import (
    MINIMUM_DATASET_CASES,
    build_gate_failures,
    evaluate,
    ranked_routing_hits,
)
from evals.loader import load_suite
from evals.models import (
    DATASET_VERSION,
    CaseFailure,
    EvaluationReport,
    GateFailure,
    RatioMetric,
)
from evals.run import main


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
    monkeypatch.setattr("evals.run.evaluate", lambda _: failed_report)

    assert main([]) == 1
    assert '"passed": false' in capsys.readouterr().out
