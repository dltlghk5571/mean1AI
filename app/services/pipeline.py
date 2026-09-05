from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

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
from app.services import ai_queue
from app.services.audit import record_audit
from app.services.classifier import (
    Classifier,
    ClassifierError,
    DepartmentCatalog,
    RuleBasedClassifier,
)
from app.services.department_catalog import ensure_current_catalog
from app.services.draft import CitationEnforcedDrafter, DraftResult
from app.services.duplicates import refresh_duplicate_candidates, sync_location_review
from app.services.emergency import detect_emergency
from app.services.knowledge import KnowledgeRetriever, RetrievalResult
from app.services.pii import redact_pii
from app.services.policy import evaluate_policy


@dataclass(frozen=True)
class PreparedTriage:
    classification: ClassificationResult
    retrieval: RetrievalResult
    draft: DraftResult


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

    @property
    def deferred(self) -> bool:
        return self.settings.ai_deferred_enabled and self.settings.ai_provider != "rules"

    def create_and_process(
        self,
        db: Session,
        payload: ComplaintCreate,
        *,
        actor_id: str | None = None,
        commit: bool = True,
    ) -> Complaint:
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
        if self.deferred:
            self._enqueue_processing(db, complaint, request_key="initial", actor_id=actor_id)
        else:
            self._process_existing(db, complaint, action="initial_processing")
        if commit:
            db.commit()
            db.refresh(complaint)
        return complaint

    def _require_routing_catalog(self, db: Session) -> None:
        try:
            ensure_current_catalog(db, self.catalog)
        except ValueError as exc:
            reason = (
                str(exc)
                if str(exc) in {"catalog_not_imported", "catalog_superseded"}
                else "catalog_not_effective"
            )
            raise ClassifierError(reason) from exc

    def reprocess(
        self,
        db: Session,
        complaint: Complaint,
        *,
        request_key: str | None = None,
        actor_id: str | None = None,
    ) -> Complaint:
        ai_queue.lock_complaint(db, complaint)
        key = request_key or str(uuid4())
        # A repeated request, or a mode switch while work is in flight, never resets its budget.
        if ai_queue.existing_request(db, complaint.id, key) is not None:
            db.commit()
            return complaint
        if self.deferred:
            self._enqueue_processing(db, complaint, request_key=key, actor_id=actor_id)
        else:
            self._process_existing(db, complaint, action="manual_reprocess")
        db.commit()
        db.refresh(complaint)
        return complaint

    def deferred_safety_reasons(self, complaint: Complaint) -> list[str]:
        combined = "\n".join(
            redact_pii(value).text
            for value in (complaint.title, complaint.content, complaint.location_text or "")
        )
        reasons = evaluate_policy(combined, complaint.category or "other").reasons
        for candidate in complaint.candidate_departments:
            department = self.catalog.by_id.get(candidate.get("department_id", ""))
            if department is not None:
                reasons.extend(evaluate_policy("", department.category).reasons)
        if detect_emergency(combined).urgency != Urgency.NORMAL or complaint.urgency != "normal":
            reasons = [*reasons, "urgent_safety_signal"]
        return sorted(set(reasons))

    def _enqueue_processing(
        self, db: Session, complaint: Complaint, *, request_key: str, actor_id: str | None
    ) -> None:
        # Cheap local triage stays available to officers even without a running worker.
        self._process_existing(
            db,
            complaint,
            action="deferred_preflight",
            local_only=True,
            force_review_reason="deferred_ai_requires_review",
        )
        reasons = self.deferred_safety_reasons(complaint)
        try:
            self._require_routing_catalog(db)
        except ClassifierError:
            reasons.append("catalog_unavailable")
        if reasons:
            record_audit(
                db,
                complaint_id=complaint.id,
                action="ai_job_skipped",
                actor_type="system",
                details={"reasons": reasons, "human_review_required": True},
            )
            return
        db.flush()
        ai_queue.enqueue(
            db,
            complaint,
            settings=self.settings,
            catalog=self.catalog,
            request_key=request_key,
            actor_id=actor_id,
            now=datetime.now(UTC),
        )

    def prepare_deferred(self, complaint: Complaint) -> PreparedTriage:
        """Run optional expensive providers with no database session or write lock held."""
        title = redact_pii(complaint.title).text
        text = redact_pii(complaint.content).text
        location = redact_pii(complaint.location_text or "").text or None
        classification = self.classifier.classify(title=title, text=text, location_text=location)
        # Treat free-text model fields as untrusted, including purported audit reason codes.
        classification = classification.model_copy(
            update={
                "subcategory": redact_pii(classification.subcategory).text,
                "evidence_summary": redact_pii(classification.evidence_summary).text,
                "missing_information": [
                    redact_pii(value).text for value in classification.missing_information
                ],
                "candidates": [
                    candidate.model_copy(update={"reason": redact_pii(candidate.reason).text})
                    for candidate in classification.candidates
                ],
                "review_reasons": (
                    ["provider_requires_review"] if classification.review_reasons else []
                ),
                "provider": self.settings.ai_provider,
            }
        )
        classification = self.catalog.bind_classification(classification)
        retrieval = self.retriever.retrieve(
            category=classification.category, text=f"{title}\n{text}"
        )
        draft = self.drafter.generate(
            title=title,
            location_text=location,
            classification=classification,
            documents=retrieval.documents,
        )
        # Provider-supplied metadata is untrusted too; audits receive a fixed provider label.
        draft = replace(
            draft,
            provider="rules" if self.drafter.provider.provider_name == "rules" else "deferred",
            rejected_sentences=[
                sentence.model_copy(
                    update={
                        "source_ids": [redact_pii(value).text for value in sentence.source_ids],
                        "reason": redact_pii(sentence.reason).text,
                    }
                )
                for sentence in draft.rejected_sentences
            ],
        )
        return PreparedTriage(classification, retrieval, draft)

    def apply_deferred(self, db: Session, complaint: Complaint, prepared: PreparedTriage) -> None:
        self._process_existing(
            db,
            complaint,
            action="deferred_processing",
            prepared=prepared,
            force_review_reason="deferred_ai_requires_review",
        )

    def approve(self, db: Session, complaint: Complaint, approval: ComplaintApproval) -> Complaint:
        approval_redaction = redact_pii(approval.answer_draft)
        if approval_redaction.detected_types:
            raise ValueError("답변 초안에 직접 식별자 형식이 남아 있어 저장할 수 없습니다.")
        ai_queue.lock_complaint(db, complaint)
        try:
            ensure_current_catalog(db, self.catalog)
            department = db.scalar(
                select(Department).where(
                    Department.id == approval.department_id,
                    Department.active.is_(True),
                )
            )
            if department is None or approval.department_id not in self.catalog.by_id:
                raise ValueError("Unknown or inactive department")
        except ValueError as exc:
            record_audit(
                db,
                complaint_id=complaint.id,
                action="human_review_blocked",
                actor_type="officer",
                actor_id=approval.actor_id,
                details={"reason": str(exc), "catalog_version": self.catalog.catalog_version},
            )
            db.commit()
            raise

        ai_queue.supersede_for_review(
            db, complaint, now=datetime.now(UTC), actor_id=approval.actor_id
        )
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
                "source_sha256": self.catalog.source_sha256,
                "approved_work_assignment_ids": list(
                    self.catalog.work_assignment_ids_for(approval.department_id)
                ),
            },
        )
        db.commit()
        db.refresh(complaint)
        return complaint

    def _process_existing(
        self,
        db: Session,
        complaint: Complaint,
        *,
        action: str,
        local_only: bool = False,
        prepared: PreparedTriage | None = None,
        force_review_reason: str | None = None,
    ) -> None:
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
        safety_text = f"{combined_redacted}\n{complaint.redacted_location_text or ''}"
        emergency = detect_emergency(safety_text)
        complaint.emergency_signals = emergency.signals

        provider_error: str | None = None
        classifier = RuleBasedClassifier(self.catalog) if local_only else self.classifier
        try:
            self._require_routing_catalog(db)
            classification = (
                prepared.classification
                if prepared is not None
                else classifier.classify(
                    title=title_redaction.text,
                    text=content_redaction.text,
                    location_text=complaint.redacted_location_text,
                )
            )
            self._require_routing_catalog(db)
            classification = self.catalog.bind_classification(classification)
        except ClassifierError as exc:
            provider_error = (
                str(exc)
                if str(exc)
                in {
                    "catalog_not_imported",
                    "catalog_superseded",
                    "catalog_not_effective",
                    "no_active_catalog_department",
                }
                else "classifier_error"
            )
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
                review_reasons=[provider_error],
                evidence_summary="분류 제공자 오류가 발생했습니다.",
                provider=getattr(classifier, "provider_name", "unknown"),
            )

        policy = evaluate_policy(safety_text, classification.category)
        top_candidate = classification.candidates[0]
        confidence = top_candidate.confidence
        effective_urgency = max(
            (classification.urgency, emergency.urgency),
            key=lambda urgency: {Urgency.NORMAL: 0, Urgency.HIGH: 1, Urgency.CRITICAL: 2}[urgency],
        )

        review_reasons = set(classification.review_reasons)
        if force_review_reason:
            review_reasons.add(force_review_reason)
        review_reasons.update(policy.reasons)
        for candidate in classification.candidates:
            candidate_department = self.catalog.by_id.get(candidate.department_id)
            if candidate_department is not None:
                review_reasons.update(evaluate_policy("", candidate_department.category).reasons)
        if classification.requires_human_review:
            review_reasons.add("classifier_requires_review")
        if effective_urgency != Urgency.NORMAL:
            review_reasons.add("urgent_safety_signal")
        if confidence < self.settings.auto_route_threshold:
            review_reasons.add("low_routing_confidence")
        if provider_error is not None:
            review_reasons.add("classifier_unavailable")
        top_rules = [
            rule
            for rule in self.catalog.routing_rules
            if rule.department_id == top_candidate.department_id
        ]
        if any(rule.requires_location for rule in top_rules) and not (
            complaint.redacted_location_text and complaint.redacted_location_text.strip()
        ):
            review_reasons.add("location_required")
        active_projection = db.scalar(
            select(Department).where(
                Department.id == top_candidate.department_id, Department.active.is_(True)
            )
        )
        if active_projection is None:
            review_reasons.add("inactive_department_projection")
        requires_review = bool(review_reasons)

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

        if prepared is not None:
            retrieval, draft = prepared.retrieval, prepared.draft
        else:
            retrieval = self.retriever.retrieve(
                category=classification.category,
                text=combined_redacted,
            )
            drafter = CitationEnforcedDrafter(self.catalog) if local_only else self.drafter
            draft = drafter.generate(
                title=title_redaction.text,
                location_text=complaint.redacted_location_text,
                classification=classification,
                documents=retrieval.documents,
            )
        complaint.answer_draft = draft.text
        complaint.knowledge_source_ids = draft.source_ids

        if draft.requires_human_review:
            review_reasons.add("draft_requires_review")
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
            "source_sha256": self.catalog.source_sha256,
            "top_department_id": top_candidate.department_id,
            "top_work_assignment_ids": top_candidate.work_assignment_ids,
            "top_confidence": confidence,
            "policy_reasons": policy.reasons,
            "review_reasons": sorted(review_reasons),
            "emergency_signals": emergency.signals,
            "status": complaint.status,
            "source_ids": draft.source_ids,
            "grounding_status": draft.validation_status,
        }
        if provider_error:
            audit_details["provider_error"] = provider_error
        if complaint.requires_human_review:
            record_audit(
                db,
                complaint_id=complaint.id,
                action="routing_review_required",
                actor_type="system",
                details={
                    "catalog_version": self.catalog.catalog_version,
                    "source_sha256": self.catalog.source_sha256,
                    "reasons": sorted(review_reasons),
                    "external_system_connected": False,
                },
            )
        record_audit(
            db,
            complaint_id=complaint.id,
            action="triage_completed",
            actor_type="system",
            details=audit_details,
        )
        sync_location_review(db, complaint)
        refresh_duplicate_candidates(db, complaint)
