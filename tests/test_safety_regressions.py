from fastapi.testclient import TestClient

from app.schemas import ComplaintStatus, Urgency
from app.services.pii import redact_pii
from evals.loader import load_suite

REQUIRED_AUDIT_ACTIONS = {"complaint_received", "pii_redacted", "triage_completed"}
PROHIBITED_AUTOMATIC_STATUSES = {
    ComplaintStatus.ASSIGNED.value,
    ComplaintStatus.REVIEWED.value,
    "closed",
    "finalized",
    "rejected",
}


def test_sensitive_cases_never_automatically_assigned_or_finalized(
    client: TestClient,
) -> None:
    sensitive_cases = [case for case in load_suite().abstention if case.sensitive]
    assert sensitive_cases

    for case in sensitive_cases:
        response = client.post(
            "/api/v1/complaints",
            json={
                "title": case.title,
                "content": case.content,
                "location_text": case.location_text,
                "channel": case.channel.value,
            },
        )

        assert response.status_code == 201, case.id
        body = response.json()
        assert body["requires_human_review"] is True, case.id
        assert body["assigned_department_id"] is None, case.id
        assert body["status"] not in PROHIBITED_AUTOMATIC_STATUSES, case.id
        assert body["status"] in {
            ComplaintStatus.NEEDS_REVIEW.value,
            ComplaintStatus.URGENT_REVIEW.value,
        }, case.id

        detail = client.get(f"/api/v1/complaints/{body['id']}")
        assert detail.status_code == 200, case.id
        actions = {event["action"] for event in detail.json()["audit_events"]}
        assert actions >= REQUIRED_AUDIT_ACTIONS, case.id


def test_urgent_cases_never_enter_an_automatic_or_normal_queue(client: TestClient) -> None:
    urgent_cases = [
        case for case in load_suite().urgency if case.expected_urgency != Urgency.NORMAL
    ]
    assert urgent_cases

    for case in urgent_cases:
        response = client.post(
            "/api/v1/complaints",
            json={
                "title": case.title,
                "content": case.content,
                "location_text": case.location_text,
                "channel": "web",
            },
        )

        assert response.status_code == 201, case.id
        body = response.json()
        assert body["status"] == ComplaintStatus.URGENT_REVIEW.value, case.id
        assert body["urgency"] != Urgency.NORMAL.value, case.id
        assert body["requires_human_review"] is True, case.id
        assert body["assigned_department_id"] is None, case.id

        detail = client.get(f"/api/v1/complaints/{body['id']}")
        actions = {event["action"] for event in detail.json()["audit_events"]}
        assert actions >= REQUIRED_AUDIT_ACTIONS, case.id


def test_every_synthetic_pii_target_is_removed_and_typed() -> None:
    for case in load_suite().pii:
        result = redact_pii(case.text)
        for target in case.targets:
            assert target.value not in result.text, case.id
            assert target.pii_type in result.detected_types, case.id
