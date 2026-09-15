"""Optional bounded club classifier. Used by the durable worker, outside DB transactions."""

import asyncio
from threading import BoundedSemaphore
from uuid import uuid4

import httpx

from app.classifier_schemas import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    ClassifierCatalog,
    ClassifierDepartment,
    ClassifierInput,
    ClubClassifyRequest,
    ClubClassifyResponse,
    input_fingerprint,
    validate_proposal,
)
from app.config import Settings
from app.schemas import ClassificationCandidate, ClassificationResult
from app.services.classifier import ClassifierError, DepartmentCatalog
from app.services.pii import contains_direct_identifiers, redact_pii


class ClubClassifier:
    provider_name = "club"

    def __init__(
        self,
        settings: Settings,
        catalog: DepartmentCatalog,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if settings.ai_provider != "club":
            raise ValueError("club_classifier_must_be_explicit")
        self.settings = settings
        self.catalog = catalog
        self.transport = transport
        self.capacity = BoundedSemaphore(settings.classifier_max_concurrent)

    def make_request(
        self, *, title: str, text: str, location_text: str | None
    ) -> ClubClassifyRequest:
        self.catalog.ensure_effective()
        complaint = ClassifierInput(
            title=redact_pii(title).text,
            content=redact_pii(text).text,
            location_text=redact_pii(location_text or "").text,
        )
        catalog = ClassifierCatalog(
            version=self.catalog.catalog_version,
            source_sha256=self.catalog.source_sha256,
            synthetic=True,
            fallback_department_id=self.catalog.fallback_department_id,
            departments=[
                ClassifierDepartment(
                    id=item.id,
                    name=item.name,
                    category=item.category,
                    description=item.description,
                    jurisdiction=item.jurisdiction,
                    subcategories=sorted({rule.subcategory for rule in item.routing_rules}),
                )
                for item in self.catalog.departments
            ],
        )
        return ClubClassifyRequest(
            request_id=uuid4(),
            input_hash=input_fingerprint(complaint, catalog),
            model_id=self.settings.classifier_model_id or "",
            complaint=complaint,
            catalog=catalog,
        )

    def classify(self, *, title: str, text: str, location_text: str | None) -> ClassificationResult:
        if not self.capacity.acquire(blocking=False):
            raise ClassifierError("club_classifier_busy")
        try:
            request = self.make_request(title=title, text=text, location_text=location_text)
            reply = asyncio.run(self._request(request))
            if (
                reply.request_id != request.request_id
                or reply.input_hash != request.input_hash
                or reply.model_id != request.model_id
                or reply.catalog_version != request.catalog.version
                or reply.catalog_sha256 != request.catalog.source_sha256
                or reply.execution_mode != self.settings.classifier_response_mode
            ):
                raise ValueError("club_classifier_response_mismatch")
            validate_proposal(request, reply.proposal)
            proposal = reply.proposal
            candidates = [
                ClassificationCandidate(**item.model_dump()) for item in proposal.candidates
            ]
            if proposal.abstained:
                candidates = [
                    ClassificationCandidate(
                        department_id=self.catalog.fallback_department_id,
                        confidence=0,
                        reason="분류 모델이 판단을 보류해 담당자 확인이 필요합니다.",
                    )
                ]
            prefix = "[합성 응답 시연] " if reply.execution_mode == "synthetic" else ""
            result = ClassificationResult(
                category=proposal.category
                or self.catalog.by_id[self.catalog.fallback_department_id].category,
                subcategory=proposal.subcategory or "소관 확인 필요",
                urgency=proposal.urgency,
                candidates=candidates,
                missing_information=proposal.missing_information,
                requires_human_review=True,
                review_reasons=["club_classifier_requires_review"],
                evidence_summary=(prefix + proposal.evidence_summary)[:500],
                provider=self.settings.classification_provider_label,
            )
            return self.catalog.bind_classification(result)
        except Exception:
            raise ClassifierError("club_classifier_failed") from None
        finally:
            self.capacity.release()

    async def _request(self, request: ClubClassifyRequest) -> ClubClassifyResponse:
        encoded = request.model_dump_json().encode()
        # Request UUID/hash are opaque; inspect prose separately to avoid digit-run false positives.
        prose = (
            request.complaint.model_dump(),
            request.catalog.model_dump(exclude={"source_sha256"}),
            request.model_id,
        )
        token = self.settings.classifier_api_key
        if len(encoded) > MAX_REQUEST_BYTES or token is None or contains_direct_identifiers(prose):
            raise ValueError("club_classifier_request_rejected")
        timeout = self.settings.classifier_request_timeout_seconds
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(timeout),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self.settings.classifier_endpoint_url or "",
                    content=encoded,
                    headers={
                        "Authorization": f"Bearer {token.get_secret_value()}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "X-Request-ID": str(request.request_id),
                    },
                ) as reply:
                    if (
                        reply.status_code != 200
                        or reply.headers.get("content-type", "").split(";", 1)[0].strip()
                        != "application/json"
                        or reply.headers.get("content-encoding", "identity") != "identity"
                        or reply.headers.get(
                            "x-model-execution", self.settings.classifier_response_mode
                        )
                        != self.settings.classifier_response_mode
                        or int(reply.headers.get("content-length", "0")) > MAX_RESPONSE_BYTES
                    ):
                        raise ValueError("club_classifier_response_rejected")
                    body = bytearray()
                    async for chunk in reply.aiter_raw():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ValueError("club_classifier_response_too_large")
        return ClubClassifyResponse.model_validate_json(body)
