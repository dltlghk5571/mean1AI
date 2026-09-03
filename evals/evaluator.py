from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Self

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base, make_engine, make_session_factory
from app.models import Complaint
from app.schemas import Channel, ComplaintCreate, ComplaintStatus, Urgency
from app.seed import seed_departments
from app.services.classifier import DepartmentCatalog, RuleBasedClassifier
from app.services.emergency import detect_emergency
from app.services.knowledge import KnowledgeRetriever
from app.services.pii import redact_pii
from app.services.pipeline import ComplaintPipeline
from evals.loader import DEFAULT_FIXTURES_DIR, load_suite
from evals.models import (
    AbstentionCase,
    CaseFailure,
    EvaluationMetrics,
    EvaluationReport,
    GateFailure,
    PiiCase,
    RatioMetric,
    RoutingCase,
    RoutingCategoryMetrics,
    UrgencyCase,
)

MINIMUM_DATASET_CASES = 200
MINIMUM_ROUTING_TOP1 = 0.95
MINIMUM_ROUTING_TOP3 = 1.0
MAXIMUM_EMERGENCY_FALSE_POSITIVE_RATE = 0.10
_AUDIT_ACTIONS = {"complaint_received", "pii_redacted", "triage_completed"}
_AUTO_ASSIGNED_STATUSES = {ComplaintStatus.ASSIGNED.value}
_FINAL_STATUSES = {
    ComplaintStatus.REVIEWED.value,
    "closed",
    "finalized",
    "rejected",
}
_PROHIBITED_AUTOMATIC_STATUSES = _AUTO_ASSIGNED_STATUSES | _FINAL_STATUSES
_URGENCY_RANK = {Urgency.NORMAL: 0, Urgency.HIGH: 1, Urgency.CRITICAL: 2}


class EvaluationRuntime:
    """Own an isolated in-memory rules pipeline for deterministic safety checks."""

    def __init__(self) -> None:
        self.settings = Settings(
            app_env="test",
            database_url="sqlite:///:memory:",
            ai_provider="rules",
            openai_api_key=None,
            auto_route_threshold=0.90,
            log_level="WARNING",
        )
        self.engine: Engine = make_engine(self.settings.database_url)
        Base.metadata.create_all(self.engine)
        self.session: Session = make_session_factory(self.engine)()
        seed_departments(self.session, self.settings.departments_path)
        self.catalog = DepartmentCatalog.from_json(self.settings.departments_path)
        self.classifier = RuleBasedClassifier(self.catalog)
        self.pipeline = ComplaintPipeline(
            settings=self.settings,
            classifier=self.classifier,
            catalog=self.catalog,
            retriever=KnowledgeRetriever(self.settings.knowledge_dir),
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.session.close()
        self.engine.dispose()

    def process(self, case: UrgencyCase | AbstentionCase) -> Complaint:
        channel = case.channel if isinstance(case, AbstentionCase) else Channel.WEB
        return self.pipeline.create_and_process(
            self.session,
            ComplaintCreate(
                title=case.title,
                content=case.content,
                location_text=case.location_text,
                channel=channel,
            ),
        )


def ranked_routing_hits(
    expected_department_id: str, candidate_ids: Sequence[str]
) -> tuple[bool, bool]:
    return (
        bool(candidate_ids) and candidate_ids[0] == expected_department_id,
        expected_department_id in candidate_ids[:3],
    )


def _evaluate_routing(
    cases: list[RoutingCase], classifier: RuleBasedClassifier
) -> tuple[RatioMetric, RatioMetric, dict[str, RoutingCategoryMetrics], list[CaseFailure]]:
    top1_hits = 0
    top3_hits = 0
    category_counts: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    failures: list[CaseFailure] = []

    for case in cases:
        result = classifier.classify(
            title=case.title,
            text=case.content,
            location_text=case.location_text,
        )
        candidate_ids = [candidate.department_id for candidate in result.candidates]
        top1_hit, top3_hit = ranked_routing_hits(case.expected_department_id, candidate_ids)
        top1_hits += int(top1_hit)
        top3_hits += int(top3_hit)
        counts = category_counts[case.expected_category]
        counts[0] += 1
        counts[1] += int(top1_hit)
        counts[2] += int(top3_hit)

        if not top1_hit:
            failures.append(
                CaseFailure(
                    suite="routing",
                    case_id=case.id,
                    check="top1_department",
                    expected=case.expected_department_id,
                    actual=candidate_ids[0] if candidate_ids else "<none>",
                )
            )
        if not top3_hit:
            failures.append(
                CaseFailure(
                    suite="routing",
                    case_id=case.id,
                    check="top3_department",
                    expected=case.expected_department_id,
                    actual=", ".join(candidate_ids) or "<none>",
                )
            )
        if result.category != case.expected_category:
            failures.append(
                CaseFailure(
                    suite="routing",
                    case_id=case.id,
                    check="category",
                    expected=case.expected_category,
                    actual=result.category,
                )
            )

    by_category = {
        category: RoutingCategoryMetrics(
            cases=counts[0],
            top1_accuracy=RatioMetric.from_counts(counts[1], counts[0]),
            top3_accuracy=RatioMetric.from_counts(counts[2], counts[0]),
        )
        for category, counts in sorted(category_counts.items())
    }
    return (
        RatioMetric.from_counts(top1_hits, len(cases)),
        RatioMetric.from_counts(top3_hits, len(cases)),
        by_category,
        failures,
    )


def _has_required_audit(complaint: Complaint) -> bool:
    actions = {event.action for event in complaint.audit_events}
    return actions >= _AUDIT_ACTIONS


def _evaluate_urgency(
    cases: list[UrgencyCase], runtime: EvaluationRuntime
) -> tuple[RatioMetric, RatioMetric, int, int, int, list[CaseFailure]]:
    true_positives = 0
    positive_total = 0
    false_positives = 0
    negative_total = 0
    auto_assigned = 0
    not_reviewed = 0
    unaudited = 0
    failures: list[CaseFailure] = []

    for case in cases:
        detection = detect_emergency(f"{case.title}\n{case.content}")
        expected_positive = case.expected_urgency != Urgency.NORMAL
        predicted_positive = detection.urgency != Urgency.NORMAL

        if expected_positive:
            positive_total += 1
            true_positives += int(predicted_positive)
            if not predicted_positive:
                failures.append(
                    CaseFailure(
                        suite="urgency",
                        case_id=case.id,
                        check="emergency_detection",
                        expected=case.expected_urgency.value,
                        actual=detection.urgency.value,
                    )
                )
            if _URGENCY_RANK[detection.urgency] < _URGENCY_RANK[case.expected_urgency]:
                failures.append(
                    CaseFailure(
                        suite="urgency",
                        case_id=case.id,
                        check="urgency_severity",
                        expected=case.expected_urgency.value,
                        actual=detection.urgency.value,
                    )
                )
            missing_signals = sorted(set(case.expected_signals) - set(detection.signals))
            if missing_signals:
                failures.append(
                    CaseFailure(
                        suite="urgency",
                        case_id=case.id,
                        check="emergency_signal",
                        expected=", ".join(case.expected_signals),
                        actual=", ".join(detection.signals) or "<none>",
                    )
                )

            complaint = runtime.process(case)
            if complaint.assigned_department_id is not None or complaint.status in (
                _PROHIBITED_AUTOMATIC_STATUSES
            ):
                auto_assigned += 1
                failures.append(
                    CaseFailure(
                        suite="urgency",
                        case_id=case.id,
                        check="urgent_auto_assignment",
                        expected="unassigned human review",
                        actual=f"{complaint.status}:{complaint.assigned_department_id}",
                    )
                )
            if complaint.status != ComplaintStatus.URGENT_REVIEW.value:
                not_reviewed += 1
                failures.append(
                    CaseFailure(
                        suite="urgency",
                        case_id=case.id,
                        check="urgent_review_queue",
                        expected=ComplaintStatus.URGENT_REVIEW.value,
                        actual=complaint.status,
                    )
                )
            if not _has_required_audit(complaint):
                unaudited += 1
                failures.append(
                    CaseFailure(
                        suite="urgency",
                        case_id=case.id,
                        check="audit_events",
                        expected=", ".join(sorted(_AUDIT_ACTIONS)),
                        actual=", ".join(event.action for event in complaint.audit_events),
                    )
                )
        else:
            negative_total += 1
            false_positives += int(predicted_positive)
            if predicted_positive:
                failures.append(
                    CaseFailure(
                        suite="urgency",
                        case_id=case.id,
                        check="emergency_false_positive",
                        expected=Urgency.NORMAL.value,
                        actual=detection.urgency.value,
                    )
                )

    return (
        RatioMetric.from_counts(true_positives, positive_total),
        RatioMetric.from_counts(false_positives, negative_total),
        auto_assigned,
        not_reviewed,
        unaudited,
        failures,
    )


def _evaluate_pii(
    cases: list[PiiCase],
) -> tuple[RatioMetric, dict[str, RatioMetric], list[CaseFailure]]:
    matched = 0
    total = 0
    type_counts: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
    failures: list[CaseFailure] = []

    for case in cases:
        result = redact_pii(case.text)
        for target in case.targets:
            total += 1
            type_counts[target.pii_type][1] += 1
            target_removed = target.value not in result.text
            type_detected = target.pii_type in result.detected_types
            correct = target_removed and type_detected
            matched += int(correct)
            type_counts[target.pii_type][0] += int(correct)
            if not correct:
                failures.append(
                    CaseFailure(
                        suite="pii",
                        case_id=case.id,
                        check=f"mask_{target.pii_type}",
                        expected="token removed and type detected",
                        actual=f"removed={target_removed}, type_detected={type_detected}",
                    )
                )

    by_type = {
        pii_type: RatioMetric.from_counts(counts[0], counts[1])
        for pii_type, counts in sorted(type_counts.items())
    }
    return RatioMetric.from_counts(matched, total), by_type, failures


def _evaluate_abstention(
    cases: list[AbstentionCase], runtime: EvaluationRuntime
) -> tuple[RatioMetric, int, int, int, list[CaseFailure]]:
    abstained = 0
    sensitive_auto_assigned = 0
    sensitive_auto_finalized = 0
    unaudited = 0
    failures: list[CaseFailure] = []

    for case in cases:
        complaint = runtime.process(case)
        safe_abstention = (
            complaint.requires_human_review
            and complaint.assigned_department_id is None
            and complaint.status
            in {ComplaintStatus.NEEDS_REVIEW.value, ComplaintStatus.URGENT_REVIEW.value}
        )
        abstained += int(safe_abstention)
        if not safe_abstention:
            failures.append(
                CaseFailure(
                    suite="abstention",
                    case_id=case.id,
                    check="human_review_abstention",
                    expected="unassigned human review",
                    actual=f"{complaint.status}:{complaint.assigned_department_id}",
                )
            )

        if case.sensitive and (
            complaint.assigned_department_id is not None
            or complaint.status in _AUTO_ASSIGNED_STATUSES
        ):
            sensitive_auto_assigned += 1
        if case.sensitive and complaint.status in _FINAL_STATUSES:
            sensitive_auto_finalized += 1
        if not _has_required_audit(complaint):
            unaudited += 1
            failures.append(
                CaseFailure(
                    suite="abstention",
                    case_id=case.id,
                    check="audit_events",
                    expected=", ".join(sorted(_AUDIT_ACTIONS)),
                    actual=", ".join(event.action for event in complaint.audit_events),
                )
            )

    return (
        RatioMetric.from_counts(abstained, len(cases)),
        sensitive_auto_assigned,
        sensitive_auto_finalized,
        unaudited,
        failures,
    )


def build_gate_failures(
    metrics: EvaluationMetrics,
    total_cases: int,
    case_failures: Sequence[CaseFailure] = (),
) -> list[GateFailure]:
    gates: list[GateFailure] = []

    def minimum(name: str, actual: float, expected: float) -> None:
        if actual < expected:
            gates.append(
                GateFailure(gate=name, expected=f">= {expected:.4f}", actual=f"{actual:.4f}")
            )

    def maximum(name: str, actual: float, expected: float) -> None:
        if actual > expected:
            gates.append(
                GateFailure(gate=name, expected=f"<= {expected:.4f}", actual=f"{actual:.4f}")
            )

    if total_cases < MINIMUM_DATASET_CASES:
        gates.append(
            GateFailure(
                gate="minimum_dataset_cases",
                expected=f">= {MINIMUM_DATASET_CASES}",
                actual=str(total_cases),
            )
        )
    minimum("routing_top1_accuracy", metrics.routing_top1_accuracy.value, MINIMUM_ROUTING_TOP1)
    minimum("routing_top3_accuracy", metrics.routing_top3_accuracy.value, MINIMUM_ROUTING_TOP3)
    minimum("emergency_recall", metrics.emergency_recall.value, 1.0)
    maximum(
        "emergency_false_positive_rate",
        metrics.emergency_false_positive_rate.value,
        MAXIMUM_EMERGENCY_FALSE_POSITIVE_RATE,
    )
    minimum("pii_masking_recall", metrics.pii_masking_recall.value, 1.0)
    minimum("abstention_rate", metrics.abstention_rate.value, 1.0)

    zero_count_gates = {
        "sensitive_auto_assigned_count": metrics.sensitive_auto_assigned_count,
        "sensitive_auto_finalized_count": metrics.sensitive_auto_finalized_count,
        "urgent_auto_assigned_count": metrics.urgent_auto_assigned_count,
        "urgent_not_reviewed_count": metrics.urgent_not_reviewed_count,
        "unaudited_processing_count": metrics.unaudited_processing_count,
    }
    for name, actual in zero_count_gates.items():
        if actual:
            gates.append(GateFailure(gate=name, expected="0", actual=str(actual)))

    safety_case_failures = [
        failure
        for failure in case_failures
        if failure.suite in {"pii", "abstention"}
        or (failure.suite == "urgency" and failure.check != "emergency_false_positive")
    ]
    if safety_case_failures:
        gates.append(
            GateFailure(
                gate="case_level_safety_failures",
                expected="0",
                actual=str(len(safety_case_failures)),
            )
        )
    return gates


def evaluate(fixtures_dir: Path = DEFAULT_FIXTURES_DIR) -> EvaluationReport:
    suite = load_suite(fixtures_dir)
    failures: list[CaseFailure] = []

    with EvaluationRuntime() as runtime:
        routing_top1, routing_top3, routing_by_category, routing_failures = _evaluate_routing(
            suite.routing, runtime.classifier
        )
        failures.extend(routing_failures)

        (
            emergency_recall,
            emergency_false_positive_rate,
            urgent_auto_assigned,
            urgent_not_reviewed,
            urgency_unaudited,
            urgency_failures,
        ) = _evaluate_urgency(suite.urgency, runtime)
        failures.extend(urgency_failures)

        pii_recall, pii_by_type, pii_failures = _evaluate_pii(suite.pii)
        failures.extend(pii_failures)

        (
            abstention_rate,
            sensitive_auto_assigned,
            sensitive_auto_finalized,
            abstention_unaudited,
            abstention_failures,
        ) = _evaluate_abstention(suite.abstention, runtime)
        failures.extend(abstention_failures)

    metrics = EvaluationMetrics(
        routing_top1_accuracy=routing_top1,
        routing_top3_accuracy=routing_top3,
        routing_by_category=routing_by_category,
        emergency_recall=emergency_recall,
        emergency_false_positive_rate=emergency_false_positive_rate,
        pii_masking_recall=pii_recall,
        pii_masking_recall_by_type=pii_by_type,
        abstention_rate=abstention_rate,
        sensitive_auto_assigned_count=sensitive_auto_assigned,
        sensitive_auto_finalized_count=sensitive_auto_finalized,
        urgent_auto_assigned_count=urgent_auto_assigned,
        urgent_not_reviewed_count=urgent_not_reviewed,
        unaudited_processing_count=urgency_unaudited + abstention_unaudited,
    )
    gate_failures = build_gate_failures(metrics, suite.total_cases, failures)
    return EvaluationReport(
        dataset_version=suite.routing[0].dataset_version,
        provider="rules",
        case_counts={
            "routing": len(suite.routing),
            "urgency": len(suite.urgency),
            "pii": len(suite.pii),
            "abstention": len(suite.abstention),
        },
        total_cases=suite.total_cases,
        metrics=metrics,
        failures=failures,
        gate_failures=gate_failures,
        passed=not gate_failures,
    )
