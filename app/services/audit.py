from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record_audit(
    db: Session,
    *,
    complaint_id: str,
    action: str,
    actor_type: str,
    details: dict[str, Any],
    actor_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        complaint_id=complaint_id,
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        details=details,
    )
    db.add(event)
    return event
