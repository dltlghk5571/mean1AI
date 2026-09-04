from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Complaint, Department
from app.schemas import (
    ClassificationCandidate,
    ClassificationResult,
    ComplaintApproval,
    ComplaintCreate,
    ComplaintStatus,
    Urgency,
)
from app.services.audit import record_audit
from app.services.classifier import Classifier, ClassifierError, DepartmentCatalog
from app.services.draft import GroundedTemplateDrafter
from app.services.duplicates import refresh_duplicate_candidates, sync_location_review
from app.services.emergency import detect_emergency
from app.services.knowledge import KnowledgeRetriever
from app.services.pii import redact_pii
from app.services.policy import evaluate_policy


class ComplaintPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        classifier: Classifier,
        catalog: DepartmentCatalog,
        retriever: KnowledgeRetriever,
    ) -> None:
        self.settings = settings
        self.classifier = classifier
        self.catalog = catalog
        self.retriever = retriever
        self.drafter = GroundedTemplateDrafter(catalog)

    def create_and_process(self, db: Session, payload: ComplaintCreate) -> Complaint:
        complaint = Complaint(
            title=payload.title,
            content=payload.content,
            location_text=payload.location_text,
            channel=payload.channel.value,
            status=ComplaintStatus.RECEIVED.value,
        )
        db.add(complaint)
        db.flush()
        record_audit(
            db,
            complaint_id=complaint.id,
            action="complaint_received",
            actor_type="citizen",
            details={"channel": payload.channel.value, "has_location": bool(payload.location_text)},
        )
        self._process_existing(db, complaint, action="initial_processing")
        db.commit()
        db.refresh(complaint)
        return complaint

    def reprocess(self, db: Session, complaint: Complaint) -> Complaint:
        self._process_existing(db, complaint, action="manual_reprocess")
        db.commit()
        db.refresh(complaint)
        return complaint

    def approve(self, db: Session, complaint: Complaint, approval: ComplaintApproval) -> Complaint:
        department = db.scalar(
            select(Department).where(
                Department.id == approval.department_id,
                Department.active.is_(True),
            )
        )
        if department is None:
            raise ValueError("Unknown or inactive department")

        old_department = complaint.assigned_department_id
        draft_modified = complaint.answer_draft != approval.answer_draft
        complaint.assigned_department_id = approval.department_id
        complaint.answer_draft = approval.answer_draft
        complaint.status = ComplaintStatus.REVIEWED.value
        complaint.requires_human_review = False
        complaint.reviewed_by = approval.actor_id
        complaint.reviewed_at = datetime.now(UTC)
        record_audit(
            db,
            complaint_id=complaint.id,
            action="human_review_approved",
            actor_type="officer",
            actor_id=approval.actor_id,
            details={
                "previous_department_id": old_department,
                "approved_department_id": approval.department_id,
                "draft_modified": draft_modified,
            },
        )
        db.commit()
        db.refresh(complaint)
        return complaint

    def _process_existing(self, db: Session, complaint: Complaint, *, action: str) -> None:
        complaint.reviewed_by = None
        complaint.reviewed_at = None
        title_redaction = redact_pii(complaint.title)
        content_redaction = redact_pii(complaint.content)
        location_redaction = redact_pii(complaint.location_text or "")
        complaint.redacted_title = title_redaction.text
        complaint.redacted_content = content_redaction.text
        complaint.redacted_location_text = location_redaction.text or None
        pii_types = sorted(
            set(
                title_redaction.detected_types
                + content_redaction.detected_types
                + location_redaction.detected_types
            )
        )
        pii_counts = dict(title_redaction.counts)
        for result in (content_redaction, location_redaction):
            for pii_type, count in result.counts.items():
                pii_counts[pii_type] = pii_counts.get(pii_type, 0) + count
        complaint.pii_types = pii_types
        record_audit(
            db,
            complaint_id=complaint.id,
            action="pii_redacted",
            actor_type="system",
            details={"detected_types": pii_types, "counts": pii_counts},
        )

        combined_redacted = f"{title_redaction.text}\n{content_redaction.text}"
        emergency = detect_emergency(combined_redacted)
        complaint.emergency_signals = emergency.signals

        provider_error: str | None = None
        try:
            classification = self.classifier.classify(
                title=title_redaction.text,
                text=content_redaction.text,
                location_text=complaint.redacted_location_text,
            )
        except ClassifierError as exc:
            provider_error = str(exc)
            classification = ClassificationResult(
                category="other",
                subcategory="AI 분류 실패 — 사람 검토 필요",
                urgency=Urgency.NORMAL,
                candidates=[
                    ClassificationCandidate(
                        department_id="CIVIL_COORDINATION",
                        confidence=0.0,
                        reason="분류 제공자 오류로 자동 판단하지 않음",
                    )
                ],
                missing_information=[],
                requires_human_review=True,
                evidence_summary="분류 제공자 오류가 발생했습니다.",
                provider=getattr(self.classifier, "provider_name", "unknown"),
            )

        policy = evaluate_policy(combined_redacted, classification.category)
        top_candidate = classification.candidates[0]
        confidence = top_candidate.confidence
        effective_urgency = max(
            (classification.urgency, emergency.urgency),
            key=lambda urgency: {Urgency.NORMAL: 0, Urgency.HIGH: 1, Urgency.CRITICAL: 2}[urgency],
        )

        requires_review = (
            classification.requires_human_review
            or policy.requires_human_review
            or effective_urgency != Urgency.NORMAL
            or confidence < self.settings.auto_route_threshold
            or provider_error is not None
        )

        complaint.category = classification.category
        complaint.subcategory = classification.subcategory
        complaint.urgency = effective_urgency.value
        complaint.routing_confidence = confidence
        complaint.classifier_provider = classification.provider
        complaint.classifier_evidence = classification.evidence_summary
        complaint.candidate_departments = [
            candidate.model_dump(mode="json") for candidate in classification.candidates
        ]
        complaint.missing_information = classification.missing_information
        complaint.requires_human_review = requires_review

        if effective_urgency != Urgency.NORMAL:
            complaint.status = ComplaintStatus.URGENT_REVIEW.value
            complaint.assigned_department_id = None
        elif requires_review:
            complaint.status = ComplaintStatus.NEEDS_REVIEW.value
            complaint.assigned_department_id = None
        else:
            complaint.status = ComplaintStatus.ASSIGNED.value
            complaint.assigned_department_id = top_candidate.department_id

        documents = self.retriever.retrieve(
            category=classification.category,
            text=combined_redacted,
        )
        draft = self.drafter.generate(
            title=title_redaction.text,
            location_text=complaint.redacted_location_text,
            classification=classification,
            documents=documents,
        )
        complaint.answer_draft = draft.text
        complaint.knowledge_source_ids = draft.source_ids

        audit_details: dict[str, object] = {
            "processing_action": action,
            "provider": classification.provider,
            "category": classification.category,
            "top_department_id": top_candidate.department_id,
            "top_confidence": confidence,
            "policy_reasons": policy.reasons,
            "emergency_signals": emergency.signals,
            "status": complaint.status,
            "source_ids": draft.source_ids,
        }
        if provider_error:
            audit_details["provider_error"] = provider_error
        record_audit(
            db,
            complaint_id=complaint.id,
            action="triage_completed",
            actor_type="system",
            details=audit_details,
        )
        sync_location_review(db, complaint)
        refresh_duplicate_candidates(db, complaint)
