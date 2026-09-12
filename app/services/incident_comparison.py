"""Opt-in model advisory with bounded HTTP, source quotes and fresh-data/audit checks."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from threading import BoundedSemaphore
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.incident_compare_schemas import (
    ComparisonInput,
    ComparisonProposal,
    ComparisonRequest,
    ComparisonResponse,
    Relation,
)
from app.models import Complaint, DuplicateCandidate, IncidentComparison
from app.services.audit import record_audit
from app.services.auth import AuthenticatedUser, require_role
from app.services.incidents import IncidentError, eligibility_error, membership
from app.services.pii import redact_pii

RELATION_LABELS = {
    "same_incident": "같은 사건 가능성",
    "related_distinct": "관련 있지만 별개",
    "recurrence": "재발 가능성",
    "uncertain": "판단 보류",
}
GUARD_LABELS = {
    "truncated_input": "긴 본문의 일부만 전달되어 판단을 보류합니다. 전체 내용을 확인해 주세요.",
    "completed_field_task": "조치 완료 사건이 포함되어 있습니다. 재발 여부를 확인해 주세요.",
}
MAX_REQUEST_BYTES = 32_000
MAX_RESPONSE_BYTES = 12_000


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def snapshot(db: Session, complaint_id: str, candidate_id: str) -> dict[str, Any]:
    if complaint_id == candidate_id:
        raise IncidentError("서로 다른 민원을 선택해 주세요.")
    pair = db.scalar(
        select(DuplicateCandidate).where(
            DuplicateCandidate.complaint_id == complaint_id,
            DuplicateCandidate.candidate_complaint_id == candidate_id,
        )
    )
    if pair is None:
        raise IncidentError("현재 민원의 비교 후보를 찾을 수 없습니다.", 404)
    if pair.status == "rejected":
        raise IncidentError("담당자가 제외한 후보입니다. 기존 검토 결과를 확인해 주세요.")
    records: list[ComparisonInput] = []
    full: list[dict[str, Any]] = []
    blocked: str | None = None
    for ref, identifier in (("current", complaint_id), ("candidate", candidate_id)):
        item = db.get(Complaint, identifier)
        if item is None:
            raise IncidentError("민원을 찾을 수 없습니다.", 404)
        blocked = blocked or eligibility_error(item)
        incident = membership(db, identifier)
        content = redact_pii(item.redacted_content or item.content).text
        values = {
            "ref": ref,
            "title": redact_pii(item.redacted_title or item.title).text,
            "content": content[:4000],
            "location": redact_pii(item.redacted_location_text or item.location_text or "").text,
            "content_truncated": len(content) > 4000,
            "category": item.category or "other",
            "submitted_at": _utc(item.created_at),
            "urgency": item.urgency,
            "field_status": incident.status if incident else "none",
        }
        record = ComparisonInput.model_validate(values)
        records.append(record)
        full.append(
            {
                **record.model_dump(mode="json"),
                "content": content,
                "complaint_status": item.status,
                "incident_id": incident.id if incident else None,
                "incident_revision": incident.revision if incident else None,
            }
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "contract": "incident-compare-v1",
                "records": full,
                "pair_status": pair.status,
                "pair_reviewed_at": pair.reviewed_at.isoformat() if pair.reviewed_at else None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "current": records[0],
        "candidate": records[1],
        "input_hash": fingerprint,
        "blocked": blocked,
    }


def validate_proposal(request: ComparisonRequest, response: ComparisonResponse) -> dict[str, Any]:
    if (response.request_id, response.input_hash, response.model_id) != (
        request.request_id,
        request.input_hash,
        request.model_id,
    ):
        raise ValueError("comparison_response_mismatch")
    proposal = response.proposal
    if redact_pii(proposal.model_dump_json()).detected_types:
        raise ValueError("comparison_response_contains_identifier")
    for evidence in proposal.evidence:
        source = request.current if evidence.ref == "current" else request.candidate
        if evidence.quote not in getattr(source, evidence.field):
            raise ValueError("comparison_ungrounded_quote")
    if proposal.relation != Relation.UNCERTAIN and {item.ref for item in proposal.evidence} != {
        "current",
        "candidate",
    }:
        raise ValueError("comparison_requires_both_sources")
    guards = []
    if request.current.content_truncated or request.candidate.content_truncated:
        guards.append("truncated_input")
    if proposal.relation == Relation.SAME and "resolved" in {
        request.current.field_status,
        request.candidate.field_status,
    }:
        guards.append("completed_field_task")
    result = proposal.model_dump(mode="json")
    if guards:
        result["relation"] = Relation.UNCERTAIN.value
        result["summary"] = "추가 확인이 필요해 같은 사건 여부를 보류했습니다."
    return {**result, "guards": guards}


class ClubIncidentComparator:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def compare(self, request: ComparisonRequest) -> ComparisonResponse:
        try:
            return asyncio.run(self._request(request))
        except Exception:
            raise ValueError("incident_comparison_model_failed") from None

    async def _request(self, request: ComparisonRequest) -> ComparisonResponse:
        if self.settings.incident_compare_provider != "club":
            raise ValueError("incident_comparison_club_must_be_explicit")
        encoded = request.model_dump_json().encode()
        key = self.settings.incident_compare_api_key
        # Opaque hashes/UUIDs can coincidentally contain phone-shaped digit runs.
        # Inspect human-readable fields, never mutate or classify request identity as PII.
        prose = json.dumps(
            [request.model_id]
            + [
                getattr(item, field)
                for item in (request.current, request.candidate)
                for field in ("title", "content", "location", "category")
            ],
            ensure_ascii=False,
        )
        if len(encoded) > MAX_REQUEST_BYTES or key is None or redact_pii(prose).detected_types:
            raise ValueError("incident_comparison_request_rejected")
        timeout = self.settings.incident_compare_timeout_seconds
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(timeout),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self.settings.incident_compare_endpoint_url or "",
                    content=encoded,
                    headers={
                        "Authorization": f"Bearer {key.get_secret_value()}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                ) as reply:
                    if (
                        reply.status_code != 200
                        or reply.headers.get("content-type", "").split(";", 1)[0].strip()
                        != "application/json"
                        or reply.headers.get("content-encoding", "identity") != "identity"
                        or reply.headers.get("x-model-execution", "model") != "model"
                        or int(reply.headers.get("content-length", "0")) > MAX_RESPONSE_BYTES
                    ):
                        raise ValueError("incident_comparison_response_rejected")
                    body = bytearray()
                    async for chunk in reply.aiter_raw():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ValueError("incident_comparison_response_too_large")
        return ComparisonResponse.model_validate_json(body)


class IncidentComparisonRunner:
    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker[Session],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.model = ClubIncidentComparator(settings, transport=transport)
        self.capacity = BoundedSemaphore(settings.incident_compare_max_concurrent)

    @property
    def provider(self) -> str:
        return self.settings.incident_compare_provider

    @property
    def model_id(self) -> str:
        return (
            "synthetic-comparison-demo-v1"
            if self.provider == "demo"
            else (self.settings.incident_compare_model_id or "unconnected")
        )

    def run(
        self, user: AuthenticatedUser, complaint_id: str, candidate_id: str, expected_hash: str
    ) -> IncidentComparison:
        require_role(user, "triage_officer", "reviewer")
        if self.provider == "off":
            raise IncidentError(
                "비교 모델이 연결되지 않았습니다. 담당자 검토를 계속할 수 있습니다.", 503
            )
        if not self.capacity.acquire(blocking=False):
            raise IncidentError("다른 비교를 진행 중입니다. 잠시 후 다시 시도해 주세요.", 429)
        try:
            return self._run(user, complaint_id, candidate_id, expected_hash)
        finally:
            self.capacity.release()

    def _run(
        self, user: AuthenticatedUser, complaint_id: str, candidate_id: str, expected_hash: str
    ) -> IncidentComparison:
        request_id = uuid4()
        with self.sessions() as db:
            if db.get_bind().dialect.name != "sqlite":
                raise IncidentError("현재 비교 검토는 SQLite 시연 환경에서만 지원합니다.", 503)
            db.execute(text("BEGIN IMMEDIATE"))
            initial = snapshot(db, complaint_id, candidate_id)
            if initial["blocked"]:
                raise IncidentError(initial["blocked"])
            if initial["input_hash"] != expected_hash:
                raise IncidentError(
                    "민원 내용이나 검토 상태가 바뀌었습니다. 새로고침해 확인해 주세요.", 409
                )
            request = ComparisonRequest(
                request_id=request_id,
                model_id=self.model_id,
                input_hash=expected_hash,
                current=initial["current"],
                candidate=initial["candidate"],
            )
            # Record model access before releasing the short snapshot transaction. No content,
            # tokens, URLs, evidence excerpts or returned prose go into complaint audits.
            self._audit(
                db, user, complaint_id, candidate_id, str(request_id), expected_hash, "requested"
            )
            db.commit()

        result: dict[str, Any] | None = None
        status = "ready"
        try:
            if self.provider == "demo":
                response = ComparisonResponse(
                    schema_version="1",
                    request_id=request.request_id,
                    input_hash=request.input_hash,
                    model_id=request.model_id,
                    proposal=ComparisonProposal(
                        relation=Relation.UNCERTAIN,
                        summary=(
                            "연결 흐름을 확인하는 합성 응답입니다. "
                            "실제 모델 판단은 수행하지 않았어요."
                        ),
                        evidence=[],
                        differences=[],
                        questions=["같은 시설의 같은 발생 상황인지 확인해 주세요."],
                    ),
                )
            else:
                response = self.model.compare(request)
            result = validate_proposal(request, response)
        except Exception:
            status = "failed"

        with self.sessions() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            try:
                current = snapshot(db, complaint_id, candidate_id)
                changed = current["input_hash"] != expected_hash or bool(current["blocked"])
            except IncidentError:
                changed = True
            if changed:
                status, result = "stale", None
            record = IncidentComparison(
                id=str(request_id),
                complaint_id=complaint_id,
                candidate_complaint_id=candidate_id,
                input_hash=expected_hash,
                provider=self.provider,
                model_id=self.model_id,
                status=status,
                result=result,
                actor_id=user.username,
            )
            db.add(record)
            self._audit(db, user, complaint_id, candidate_id, record.id, expected_hash, status)
            db.commit()
            return record

    def _audit(
        self,
        db: Session,
        user: AuthenticatedUser,
        complaint_id: str,
        candidate_id: str,
        comparison_id: str,
        input_hash: str,
        status: str,
    ) -> None:
        for identifier in (complaint_id, candidate_id):
            record_audit(
                db,
                complaint_id=identifier,
                action="incident_comparison_" + status,
                actor_type="officer" if status == "requested" else "system",
                actor_id=user.username,
                details={
                    "comparison_id": comparison_id,
                    "input_hash": input_hash,
                    "provider": self.provider,
                    "model_id": self.model_id,
                    "status": status,
                    "automatic_link": False,
                },
            )

    def view(self, db: Session, complaint_id: str, candidate_id: str) -> dict[str, Any]:
        current = snapshot(db, complaint_id, candidate_id)
        latest = db.scalar(
            select(IncidentComparison)
            .where(
                IncidentComparison.complaint_id == complaint_id,
                IncidentComparison.candidate_complaint_id == candidate_id,
            )
            .order_by(IncidentComparison.created_at.desc(), IncidentComparison.id.desc())
            .limit(1)
        )
        stale = latest is not None and (
            latest.input_hash != current["input_hash"]
            or latest.provider != self.provider
            or latest.model_id != self.model_id
        )
        return {
            **current,
            "latest": latest,
            "result": latest.result if latest and not stale else None,
            "result_stale": stale,
            "provider": self.provider,
            "model_id": self.model_id,
            "complaint_id": complaint_id,
            "candidate_id": candidate_id,
            "relation_labels": RELATION_LABELS,
            "guard_labels": GUARD_LABELS,
        }
