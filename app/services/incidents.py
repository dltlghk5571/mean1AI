"""Explicit shared field-task membership and publication, without changing complaint outcomes."""

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.incident_schemas import (
    ChangeIncidentStatus,
    CreateIncident,
    IncidentInput,
    IncidentRevision,
    LinkComplaint,
    PublishIncident,
    UnlinkComplaint,
)
from app.models import Complaint, ComplaintIncidentLink, DuplicateCandidate, Incident, IncidentEvent
from app.services.audit import record_audit
from app.services.auth import AuthenticatedUser, require_role
from app.services.pii import redact_pii
from app.services.policy import evaluate_policy

STATUS_LABELS = {"checking": "현장 확인 중", "in_progress": "조치 중", "resolved": "현장 조치 완료"}
ACTION_LABELS = {
    "created": "사건 생성",
    "linked": "민원 연결",
    "unlinked": "민원 분리",
    "status_changed": "현장 진행 변경",
    "published": "공통 안내 공개",
    "withdrawn": "공통 안내 공개 철회",
}
FIELD_CATEGORIES = {"streetlight", "road_damage", "waste", "park", "traffic", "water_sewer"}
PAGE_SIZE = 20
MAX_MEMBERS = 100
POLICY_VERSION = "manual-field-incident-v1"


class IncidentError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def eligibility_error(complaint: Complaint) -> str | None:
    if complaint.category not in FIELD_CATEGORIES:
        return (
            "현장 시설·환경 문제만 연결할 수 있습니다. "
            "개인별 판단이 필요한 민원은 별도로 검토하세요."
        )
    content = f"{complaint.redacted_title}\n{complaint.redacted_content}"
    if evaluate_policy(content, complaint.category).requires_human_review:
        return "개인별 자격·보상·처분 등 별도 검토가 필요한 내용이 있어 사건에 연결할 수 없습니다."
    if not complaint.redacted_location_text:
        return "발생 장소를 확인할 수 없는 민원은 연결할 수 없습니다."
    return None


def _complaint(db: Session, complaint_id: str) -> Complaint:
    result = db.get(Complaint, complaint_id)
    if result is None:
        raise IncidentError("민원을 찾을 수 없습니다.", 404)
    return result


def get_incident(db: Session, incident_id: str) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise IncidentError("사건을 찾을 수 없습니다.", 404)
    return incident


def membership(db: Session, complaint_id: str) -> Incident | None:
    return db.scalar(
        select(Incident)
        .join(ComplaintIncidentLink, ComplaintIncidentLink.incident_id == Incident.id)
        .where(ComplaintIncidentLink.complaint_id == complaint_id)
    )


def member_ids(db: Session, incident_id: str) -> list[str]:
    return list(
        db.scalars(
            select(ComplaintIncidentLink.complaint_id)
            .where(ComplaintIncidentLink.incident_id == incident_id)
            .order_by(ComplaintIncidentLink.complaint_id)
        )
    )


def _confirmed_pair(db: Session, left: str, right: str) -> tuple[Complaint, Complaint]:
    if left == right:
        raise IncidentError("서로 다른 두 민원을 선택해 주세요.")
    complaints = (_complaint(db, left), _complaint(db, right))
    for complaint in complaints:
        error = eligibility_error(complaint)
        if error:
            raise IncidentError(error)
    if complaints[0].category != complaints[1].category:
        raise IncidentError("현재 민원 분야가 다릅니다. 같은 현장 문제인지 다시 검토해 주세요.")
    pair = db.scalar(
        select(DuplicateCandidate.id).where(
            DuplicateCandidate.status == "confirmed",
            or_(
                and_(
                    DuplicateCandidate.complaint_id == left,
                    DuplicateCandidate.candidate_complaint_id == right,
                ),
                and_(
                    DuplicateCandidate.complaint_id == right,
                    DuplicateCandidate.candidate_complaint_id == left,
                ),
            ),
        )
    )
    if pair is None:
        raise IncidentError("담당자가 중복 후보로 확인한 두 민원만 연결할 수 있습니다.")
    return complaints


def prepare(db: Session, complaint_id: str, candidate_id: str) -> dict[str, Any]:
    left, right = _confirmed_pair(db, complaint_id, candidate_id)
    current, candidate = membership(db, left.id), membership(db, right.id)
    incident = current or candidate
    error = None
    if current and candidate:
        error = (
            "이미 같은 사건에 연결되어 있습니다."
            if current.id == candidate.id
            else "각각 다른 사건에 연결되어 있습니다. 필요한 민원을 분리한 뒤 검토하세요."
        )
    elif incident and incident.status == "resolved":
        error = "조치가 완료된 사건입니다. 재발인지 검토하고, 필요한 경우 진행 상태를 변경하세요."
    return {
        "left": left,
        "right": right,
        "incident": incident,
        "pair_error": error,
        "new_complaint_id": right.id if current else left.id,
        "linked_candidate_id": left.id if current else right.id,
    }


def _safe_text(value: str) -> None:
    if redact_pii(value).detected_types:
        raise IncidentError("전화번호·이메일·주민등록번호 형식을 지우고 다시 확인해 주세요.")


def _begin_write(db: Session) -> None:
    # All incident commands enter here before any query. SQLite's writer lock also serializes
    # empty memberships; row locks cannot protect a missing link. No partial transaction reset.
    if db.in_transaction():
        raise RuntimeError("Incident commands require a fresh transaction")
    if db.get_bind().dialect.name != "sqlite":
        raise IncidentError("현재 사건 연결은 SQLite 시연 환경에서만 지원합니다.", 503)
    db.execute(text("BEGIN IMMEDIATE"))


def _record(
    db: Session,
    incident: Incident,
    user: AuthenticatedUser,
    action: str,
    reason: str,
    *,
    affected: list[str],
    message: str | None = None,
) -> None:
    incident.updated_at = datetime.now(UTC)
    ids = member_ids(db, incident.id)
    db.add(
        IncidentEvent(
            incident_id=incident.id,
            revision=incident.revision,
            action=action,
            status=incident.status,
            actor_id=user.username,
            reason=reason,
            complaint_ids=ids,
            public_message=message,
        )
    )
    # Each affected original intake gets an audit, including the removed member. No free text,
    # citizen credentials, addresses or complaint bodies are duplicated into complaint audits.
    for complaint_id in sorted(set(affected)):
        record_audit(
            db,
            complaint_id=complaint_id,
            action=f"incident_{action}",
            actor_type="officer",
            actor_id=user.username,
            details={
                "incident_id": incident.id,
                "revision": incident.revision,
                "status": incident.status,
                "member_count": len(ids),
                "policy_version": POLICY_VERSION,
                "complaint_status_changed": False,
                "external_message_sent": False,
            },
        )


def execute(
    db: Session,
    user: AuthenticatedUser,
    action: str,
    data: dict[str, str],
    *,
    incident_id: str | None = None,
) -> Incident:
    """Validate and stage one atomic command. The route owns commit/rollback."""
    models: dict[str, type[IncidentInput]] = {
        "created": CreateIncident,
        "linked": LinkComplaint,
        "unlinked": UnlinkComplaint,
        "status_changed": ChangeIncidentStatus,
        "published": PublishIncident,
        "withdrawn": IncidentRevision,
    }
    if action not in models:
        raise IncidentError("지원하지 않는 작업입니다.")
    if (action == "created") != (incident_id is None):
        raise IncidentError("작업 경로를 확인해 주세요.")
    if action in {"published", "withdrawn"}:
        require_role(user, "reviewer")
    else:
        require_role(user, "triage_officer", "reviewer")
    try:
        payload = models[action].model_validate(data)
    except ValidationError:
        raise IncidentError(
            "입력 길이와 확인 항목을 확인해 주세요. 사유는 5~500자입니다.", 422
        ) from None
    _safe_text(payload.reason)
    _begin_write(db)
    if isinstance(payload, CreateIncident):
        left, right = _confirmed_pair(db, str(payload.complaint_id), str(payload.candidate_id))
        if membership(db, left.id) or membership(db, right.id):
            raise IncidentError(
                "이미 사건에 연결된 민원이 있습니다. 새로고침해 연결 상태를 확인하세요.", 409
            )
        _safe_text(payload.title)
        incident = Incident(
            title=payload.title, category=left.category, status="checking", revision=1
        )
        db.add(incident)
        db.flush()
        db.add_all(
            [
                ComplaintIncidentLink(complaint_id=item.id, incident_id=incident.id)
                for item in (left, right)
            ]
        )
        db.flush()
        _record(db, incident, user, action, payload.reason, affected=[left.id, right.id])
        return incident

    assert isinstance(payload, IncidentRevision)
    incident = get_incident(db, incident_id or "")
    if incident.revision != payload.revision:
        raise IncidentError(
            "다른 작업이 먼저 반영되었습니다. 새로고침해 최신 상태를 확인하세요.", 409
        )
    before = member_ids(db, incident.id)
    if isinstance(payload, LinkComplaint):
        new_id, candidate_id = str(payload.complaint_id), str(payload.candidate_id)
        new_complaint, _ = _confirmed_pair(db, new_id, candidate_id)
        if candidate_id not in before or membership(db, new_id):
            raise IncidentError(
                "민원의 연결 상태가 달라졌습니다. 최신 상태를 다시 확인하세요.", 409
            )
        if incident.status == "resolved":
            raise IncidentError(
                "조치가 완료된 사건에는 새 민원을 연결할 수 없습니다. 재발 여부를 검토하세요."
            )
        if new_complaint.category != incident.category:
            raise IncidentError("사건 분야와 현재 민원 분야가 다릅니다.")
        if len(before) >= MAX_MEMBERS:
            raise IncidentError("시연 환경은 사건당 100건까지 연결할 수 있습니다.")
        db.add(ComplaintIncidentLink(complaint_id=new_id, incident_id=incident.id))
        before.append(new_id)
    elif isinstance(payload, UnlinkComplaint):
        link = db.get(ComplaintIncidentLink, str(payload.complaint_id))
        if link is None or link.incident_id != incident.id:
            raise IncidentError("이 사건에 연결된 민원이 아닙니다.", 409)
        db.delete(link)
    elif isinstance(payload, ChangeIncidentStatus):
        if not before:
            raise IncidentError("연결된 민원이 없어 현장 진행을 변경할 수 없습니다.")
        if incident.status == payload.status.value:
            raise IncidentError("현재와 다른 진행 상태를 선택해 주세요.")
        incident.status = payload.status.value
    elif isinstance(payload, PublishIncident):
        if not before:
            raise IncidentError("연결된 민원이 없어 안내를 공개할 수 없습니다.")
        _safe_text(payload.message)
    elif action == "withdrawn":
        latest = current_publication(db, incident)
        if latest is None:
            raise IncidentError("현재 공개 중인 안내가 없습니다.", 409)
    incident.revision += 1
    db.flush()
    _record(
        db,
        incident,
        user,
        action,
        payload.reason,
        affected=before,
        message=payload.message if isinstance(payload, PublishIncident) else None,
    )
    return incident


def current_publication(db: Session, incident: Incident) -> IncidentEvent | None:
    return db.scalar(
        select(IncidentEvent).where(
            IncidentEvent.incident_id == incident.id,
            IncidentEvent.revision == incident.revision,
            IncidentEvent.action == "published",
        )
    )


def citizen_progress(db: Session, complaint_id: str) -> dict[str, Any] | None:
    """Call only after citizen access validation. The single query binds current membership
    and publication revision; no internal title, count, IDs or another citizen's records escape.
    """
    event = db.scalar(
        select(IncidentEvent)
        .join(Incident, Incident.id == IncidentEvent.incident_id)
        .join(ComplaintIncidentLink, ComplaintIncidentLink.incident_id == Incident.id)
        .where(
            ComplaintIncidentLink.complaint_id == complaint_id,
            IncidentEvent.revision == Incident.revision,
            IncidentEvent.action == "published",
        )
    )
    if event is None or not event.public_message or redact_pii(event.public_message).detected_types:
        return None
    return {
        "status": STATUS_LABELS[event.status],
        "message": event.public_message,
        "published_at": event.created_at,
    }


def queue(db: Session, page: int = 1, state: str = "active") -> dict[str, Any]:
    condition = (
        and_(
            Incident.status != "resolved",
            select(ComplaintIncidentLink.complaint_id)
            .where(ComplaintIncidentLink.incident_id == Incident.id)
            .correlate(Incident)
            .exists(),
        )
        if state == "active"
        else text("1=1")
    )
    total = db.scalar(select(func.count()).select_from(Incident).where(condition)) or 0
    page = min(max(1, page), max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
    rows = db.execute(
        select(Incident, func.count(ComplaintIncidentLink.complaint_id))
        .outerjoin(ComplaintIncidentLink)
        .where(condition)
        .group_by(Incident.id)
        .order_by(Incident.updated_at.desc(), Incident.id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    return {
        "items": [{"incident": incident, "count": count} for incident, count in rows],
        "total": total,
        "page": page,
        "has_next": page * PAGE_SIZE < total,
    }


def detail(db: Session, incident_id: str, event_page: int = 1) -> dict[str, Any]:
    incident = get_incident(db, incident_id)
    members = list(
        db.scalars(
            select(Complaint)
            .join(ComplaintIncidentLink, ComplaintIncidentLink.complaint_id == Complaint.id)
            .where(ComplaintIncidentLink.incident_id == incident.id)
            .order_by(Complaint.created_at, Complaint.id)
        )
    )
    total = (
        db.scalar(
            select(func.count())
            .select_from(IncidentEvent)
            .where(IncidentEvent.incident_id == incident.id)
        )
        or 0
    )
    event_page = min(max(1, event_page), max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
    events = list(
        db.scalars(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident.id)
            .order_by(IncidentEvent.revision.desc())
            .offset((event_page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    )
    return {
        "incident": incident,
        "members": members,
        "events": events,
        "event_page": event_page,
        "has_next": event_page * PAGE_SIZE < total,
        "publication": current_publication(db, incident),
    }
