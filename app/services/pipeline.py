from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Complaint, Department, GroundedDraftRecord, ReviewDecision
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
from app.services.draft import CitationEnforcedDrafter
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
        self.drafter = CitationEnforcedDrafter(catalog)

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
        approval_redaction = redact_pii(approval.answer_draft)
        if approval_redaction.detected_types:
            raise ValueError("답변 초안에 직접 식별자 형식이 남아 있어 저장할 수 없습니다.")
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
        grounding = db.get(GroundedDraftRecord, complaint.id)
        if draft_modified and grounding is not None:
            grounding.validation_status = "human_modified_unverified"
            grounding.updated_at = datetime.now(UTC)
            record_audit(
                db,
                complaint_id=complaint.id,
                action="draft_grounding_invalidated",
                actor_type="system",
                details={
                    "reason": "officer_modified_generated_text",
                    "previous_provider": grounding.provider,
                },
            )
        complaint.assigned_department_id = approval.department_id
        complaint.answer_draft = approval.answer_draft
        complaint.status = ComplaintStatus.REVIEWED.value
        complaint.requires_human_review = False
        complaint.reviewed_by = approval.actor_id
        complaint.reviewed_at = datetime.now(UTC)
        decision = ReviewDecision(
            complaint_id=complaint.id,
            actor_id=approval.actor_id,
            actor_role=approval.actor_role,
            department_id=approval.department_id,
            answer_draft=approval.answer_draft,
            draft_modified=draft_modified,
            grounding_status=(
                grounding.validation_status if grounding else "grounding_record_missing"
            ),
        )
        db.add(decision)
        db.flush()
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
                "actor_role": approval.actor_role,
                "review_decision_id": decision.id,
                "catalog_version": self.catalog.catalog_version,
                "approved_work_assignment_ids": list(
                    self.catalog.work_assignment_ids_for(approval.department_id)
                ),
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
            classification = self.catalog.bind_classification(classification)
        except ClassifierError as exc:
            provider_error = str(exc)
            classification = ClassificationResult(
                category="other",
                subcategory="AI 분류 실패 — 사람 검토 필요",
                urgency=Urgency.NORMAL,
                candidates=[
                    ClassificationCandidate(
                        department_id=self.catalog.fallback_department_id,
                        confidence=0.0,
                        reason="분류 제공자 오류로 자동 판단하지 않음",
                        catalog_version=self.catalog.catalog_version,
                        work_assignment_ids=list(
                            self.catalog.work_assignment_ids_for(
                                self.catalog.fallback_department_id
                            )
                        ),
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

        retrieval = self.retriever.retrieve(
            category=classification.category,
            text=combined_redacted,
        )
        draft = self.drafter.generate(
            title=title_redaction.text,
            location_text=complaint.redacted_location_text,
            classification=classification,
            documents=retrieval.documents,
        )
        complaint.answer_draft = draft.text
        complaint.knowledge_source_ids = draft.source_ids

        if draft.requires_human_review:
            complaint.requires_human_review = True
            complaint.assigned_department_id = None
            if effective_urgency == Urgency.NORMAL:
                complaint.status = ComplaintStatus.NEEDS_REVIEW.value

        document_snapshots = [
            {
                "id": document.id,
                "title": document.title,
                "category": document.category,
                "version": document.version,
                "effective_from": document.effective_from.isoformat(),
                "effective_until": (
                    document.effective_until.isoformat() if document.effective_until else None
                ),
                "approval_status": document.approval_status,
                "retrieval_score": retrieval.scores[document.id],
            }
            for document in retrieval.documents
        ]
        exclusion_snapshots = [
            {"document_id": exclusion.document_id, "reason": exclusion.reason}
            for exclusion in retrieval.excluded
        ]
        grounding = db.get(GroundedDraftRecord, complaint.id)
        if grounding is None:
            grounding = GroundedDraftRecord(
                complaint_id=complaint.id,
                provider=draft.provider,
                validation_status=draft.validation_status,
                sentences=[],
                rejected_sentences=[],
                retrieved_documents=[],
                retrieval_exclusions=[],
            )
            db.add(grounding)
        grounding.provider = draft.provider
        grounding.validation_status = draft.validation_status
        grounding.sentences = [sentence.model_dump(mode="json") for sentence in draft.sentences]
        grounding.rejected_sentences = [
            sentence.model_dump(mode="json") for sentence in draft.rejected_sentences
        ]
        grounding.retrieved_documents = document_snapshots
        grounding.retrieval_exclusions = exclusion_snapshots
        grounding.updated_at = datetime.now(UTC)

        exclusion_counts: dict[str, int] = {}
        for exclusion in retrieval.excluded:
            exclusion_counts[exclusion.reason] = exclusion_counts.get(exclusion.reason, 0) + 1
        record_audit(
            db,
            complaint_id=complaint.id,
            action="knowledge_retrieved",
            actor_type="system",
            details={
                "strategy": retrieval.strategy,
                "selected_source_ids": [document.id for document in retrieval.documents],
                "selected_count": len(retrieval.documents),
                "excluded_by_reason": exclusion_counts,
            },
        )
        record_audit(
            db,
            complaint_id=complaint.id,
            action="draft_grounding_validated",
            actor_type="system",
            details={
                "provider": draft.provider,
                "validation_status": draft.validation_status,
                "accepted_sentence_count": len(draft.sentences),
                "substantive_sentence_count": sum(
                    sentence.substantive for sentence in draft.sentences
                ),
                "rejected_sentence_count": len(draft.rejected_sentences),
                "cited_source_ids": draft.source_ids,
                "external_send": False,
            },
        )

        audit_details: dict[str, object] = {
            "processing_action": action,
            "provider": classification.provider,
            "category": classification.category,
            "catalog_version": self.catalog.catalog_version,
            "top_department_id": top_candidate.department_id,
            "top_work_assignment_ids": top_candidate.work_assignment_ids,
            "top_confidence": confidence,
            "policy_reasons": policy.reasons,
            "emergency_signals": emergency.signals,
            "status": complaint.status,
            "source_ids": draft.source_ids,
            "grounding_status": draft.validation_status,
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
