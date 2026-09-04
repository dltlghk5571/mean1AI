import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings
from app.services.knowledge import KnowledgeRetriever
from evals.rag_models import (
    RAG_DATASET_VERSION,
    RagEvaluationReport,
    RetrievalCase,
    RetrievalMetrics,
    RetrievalStrategyReport,
)

DEFAULT_RAG_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "rag_retrieval.jsonl"
EVALUATION_DATE = date(2026, 9, 4)


def load_retrieval_cases(path: Path = DEFAULT_RAG_FIXTURE) -> list[RetrievalCase]:
    cases: list[RetrievalCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(RetrievalCase.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"Invalid RAG fixture at {path}:{line_number}: {exc}") from exc
    if not cases:
        raise ValueError(f"RAG fixture is empty: {path}")
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("RAG fixture case IDs must be unique")
    if any(case.dataset_version != RAG_DATASET_VERSION for case in cases):
        raise ValueError("RAG fixture versions must match the evaluator version")
    return cases


def _ratio(numerator: int, denominator: int, *, empty_value: float = 0.0) -> float:
    return numerator / denominator if denominator else empty_value


def _measure(
    cases: list[RetrievalCase],
    retrieve_ids: Callable[[RetrievalCase], list[str]],
) -> RetrievalMetrics:
    true_positive = 0
    false_positive = 0
    false_negative = 0
    abstentions = 0
    direct_hits = direct_total = 0
    paraphrase_hits = paraphrase_total = 0
    irrelevant_rejections = irrelevant_total = 0

    for case in cases:
        predicted = set(retrieve_ids(case))
        expected = set(case.expected_source_ids)
        true_positive += len(predicted & expected)
        false_positive += len(predicted - expected)
        false_negative += len(expected - predicted)
        abstentions += not predicted

        hit = bool(expected) and expected.issubset(predicted)
        if case.query_kind == "direct":
            direct_total += 1
            direct_hits += hit
        elif case.query_kind == "paraphrase":
            paraphrase_total += 1
            paraphrase_hits += hit
        else:
            irrelevant_total += 1
            irrelevant_rejections += not predicted

    precision = _ratio(true_positive, true_positive + false_positive, empty_value=1.0)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return RetrievalMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        direct_recall=_ratio(direct_hits, direct_total),
        paraphrase_recall=_ratio(paraphrase_hits, paraphrase_total),
        irrelevant_rejection_rate=_ratio(irrelevant_rejections, irrelevant_total),
        abstention_rate=_ratio(abstentions, len(cases)),
        true_positive_count=true_positive,
        false_positive_count=false_positive,
        false_negative_count=false_negative,
    )


def evaluate_rag(
    fixture_path: Path = DEFAULT_RAG_FIXTURE,
    knowledge_dir: Path | None = None,
) -> RagEvaluationReport:
    cases = load_retrieval_cases(fixture_path)
    retriever = KnowledgeRetriever(knowledge_dir or Settings().knowledge_dir)

    baseline_metrics = _measure(
        cases,
        lambda case: [
            document.id
            for document in retriever.eligible_documents(
                category=case.category,
                as_of=EVALUATION_DATE,
            )[:3]
        ],
    )
    candidate_metrics = _measure(
        cases,
        lambda case: [
            document.id
            for document in retriever.retrieve(
                category=case.category,
                text=case.query_text,
                as_of=EVALUATION_DATE,
            ).documents
        ],
    )

    failures: list[str] = []
    if len(cases) != 24:
        failures.append(f"case_count: expected 24, got {len(cases)}")
    if candidate_metrics.precision != 1.0:
        failures.append(f"candidate_precision: expected 1.0, got {candidate_metrics.precision}")
    if candidate_metrics.direct_recall != 1.0:
        failures.append(
            f"candidate_direct_recall: expected 1.0, got {candidate_metrics.direct_recall}"
        )
    if candidate_metrics.irrelevant_rejection_rate != 1.0:
        failures.append(
            "candidate_irrelevant_rejection_rate: expected 1.0, "
            f"got {candidate_metrics.irrelevant_rejection_rate}"
        )
    if candidate_metrics.false_positive_count:
        failures.append(
            f"candidate_false_positive_count: expected 0, got "
            f"{candidate_metrics.false_positive_count}"
        )

    return RagEvaluationReport(
        dataset_version=RAG_DATASET_VERSION,
        case_count=len(cases),
        provider="rules",
        baseline=RetrievalStrategyReport(
            strategy="approved_effective_category_only_baseline",
            metrics=baseline_metrics,
        ),
        candidate=RetrievalStrategyReport(
            strategy="strict_lexical_v1",
            metrics=candidate_metrics,
        ),
        safety_gate_failures=failures,
        passed=not failures,
    )
