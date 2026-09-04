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

    assert len(cases) == 24
    assert len({case.id for case in cases}) == 24
    assert all(case.synthetic is True for case in cases)
    assert all(case.dataset_version == RAG_DATASET_VERSION for case in cases)
    assert Counter(case.query_kind for case in cases) == Counter(
        {"direct": 16, "paraphrase": 4, "irrelevant": 4}
    )


def test_strict_lexical_retrieval_meets_safety_gates_and_exposes_tradeoff(
    rag_report: RagEvaluationReport,
) -> None:
    baseline = rag_report.baseline.metrics
    candidate = rag_report.candidate.metrics

    assert rag_report.provider == "rules"
    assert rag_report.baseline.strategy == "approved_effective_category_only_baseline"
    assert rag_report.candidate.strategy == "strict_lexical_v1"
    assert baseline.precision == pytest.approx(20 / 24)
    assert baseline.recall == 1.0
    assert baseline.abstention_rate == 0.0
    assert candidate.precision == 1.0
    assert candidate.recall == 0.8
    assert candidate.direct_recall == 1.0
    assert candidate.paraphrase_recall == 0.0
    assert candidate.irrelevant_rejection_rate == 1.0
    assert candidate.abstention_rate == pytest.approx(8 / 24)
    assert candidate.false_positive_count == 0
    assert rag_report.safety_gate_failures == []
    assert rag_report.passed is True


def test_rag_markdown_report_states_no_embedding_tradeoff(
    rag_report: RagEvaluationReport,
) -> None:
    markdown = format_markdown(rag_report)

    assert "no embeddings" in markdown
    assert "approved_effective_category_only_baseline" in markdown
    assert "strict_lexical_v1" in markdown
    assert "83.3%" in markdown
    assert "80.0%" in markdown
    assert "Embeddings remain intentionally disabled" in markdown


def test_rag_cli_emits_machine_readable_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["passed"] is True
    assert output["candidate"]["strategy"] == "strict_lexical_v1"


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
