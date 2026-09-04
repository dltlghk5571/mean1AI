import json
from collections import Counter

import pytest

from evals.rag_evaluator import evaluate_rag, load_retrieval_cases
from evals.rag_models import RAG_DATASET_VERSION, RagEvaluationReport
from evals.rag_run import format_markdown, main


@pytest.fixture(scope="module")
def rag_report() -> RagEvaluationReport:
    return evaluate_rag()


def test_rag_fixture_is_fixed_synthetic_and_balanced() -> None:
    cases = load_retrieval_cases()

    assert len(cases) == 36
    assert len({case.id for case in cases}) == 36
    assert all(case.synthetic is True for case in cases)
    assert all(case.dataset_version == RAG_DATASET_VERSION for case in cases)
    assert Counter(case.query_kind for case in cases) == Counter(
        {"direct": 16, "paraphrase": 8, "irrelevant": 12}
    )
    assert sum(case.id.startswith("rag-hard-negative-") for case in cases) == 8


def test_offline_hybrid_improves_recall_without_weakening_safety(
    rag_report: RagEvaluationReport,
) -> None:
    baseline = rag_report.baseline.metrics
    lexical = rag_report.lexical_baseline.metrics
    candidate = rag_report.candidate.metrics

    assert rag_report.provider == "rules"
    assert rag_report.baseline.strategy == "approved_effective_category_only_baseline"
    assert rag_report.lexical_baseline.strategy == "strict_lexical_v1"
    assert rag_report.candidate.strategy == "offline_concept_hybrid_v2"
    assert baseline.precision == pytest.approx(24 / 36)
    assert baseline.recall == 1.0
    assert baseline.abstention_rate == 0.0
    assert lexical.precision == pytest.approx(16 / 19)
    assert lexical.recall == pytest.approx(16 / 24)
    assert lexical.paraphrase_recall == 0.0
    assert lexical.irrelevant_rejection_rate == pytest.approx(9 / 12)
    assert lexical.false_positive_count == 3
    assert candidate.precision == 1.0
    assert candidate.recall == 1.0
    assert candidate.direct_recall == 1.0
    assert candidate.paraphrase_recall == 1.0
    assert candidate.irrelevant_rejection_rate == 1.0
    assert candidate.abstention_rate == pytest.approx(12 / 36)
    assert candidate.false_positive_count == 0
    assert rag_report.safety_gate_failures == []
    assert rag_report.passed is True


def test_rag_markdown_report_explains_offline_hybrid(
    rag_report: RagEvaluationReport,
) -> None:
    markdown = format_markdown(rag_report)

    assert "no embeddings" in markdown
    assert "approved_effective_category_only_baseline" in markdown
    assert "strict_lexical_v1" in markdown
    assert "offline_concept_hybrid_v2" in markdown
    assert "66.7%" in markdown
    assert "100.0%" in markdown
    assert "small reviewed Korean concept map" in markdown
    assert "Embeddings remain intentionally disabled" in markdown


def test_rag_cli_emits_machine_readable_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["passed"] is True
    assert output["lexical_baseline"]["strategy"] == "strict_lexical_v1"
    assert output["candidate"]["strategy"] == "offline_concept_hybrid_v2"


def test_rag_cli_returns_nonzero_when_a_safety_gate_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rag_report: RagEvaluationReport,
) -> None:
    failed = rag_report.model_copy(
        update={"safety_gate_failures": ["synthetic regression"], "passed": False}
    )
    monkeypatch.setattr("evals.rag_run.evaluate_rag", lambda *_: failed)

    assert main([]) == 1
    assert '"passed": false' in capsys.readouterr().out
