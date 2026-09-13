"""Synthetic shared tasks: human approval, independent outcomes, privacy and transaction races."""

import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    AuditEvent,
    Complaint,
    ComplaintIncidentLink,
    DuplicateCandidate,
    Incident,
    IncidentEvent,
)
from app.services import incidents
from app.services.auth import AuthenticatedUser
from tests.test_citizen import start, submit

REASON = "합성 현장의 시설 번호와 발생 시점을 대조했습니다."
MESSAGE = "합성 현장의 시설을 점검 중이며 조치 내용을 다시 안내하겠습니다."
TITLE = "담당자용 합성 공원 조명 사건"


def seed_pair(test_app: FastAPI, count: int = 3) -> list[str]:
    with test_app.state.session_factory() as db:
        complaints = [
            Complaint(
                title=f"합성 조명 제보 {index}",
                redacted_title=f"합성 조명 제보 {index}",
                content="합성 공원 가로등이 꺼졌습니다.",
                redacted_content="합성 공원 가로등이 꺼졌습니다.",
                location_text="가상 시험공원 정문",
                redacted_location_text="가상 시험공원 정문",
                category="streetlight",
                status="urgent_review" if index == 0 else "needs_review",
                urgency="critical" if index == 0 else "normal",
            )
            for index in range(count)
        ]
        db.add_all(complaints)
        db.flush()
        for item in complaints[1:]:
            db.add(
                DuplicateCandidate(
                    complaint_id=item.id,
                    candidate_complaint_id=complaints[0].id,
                    status="confirmed",
                    reviewed_by="review.demo",
                    total_score=0.9,
                    category_score=1,
                    location_score=1,
                    time_score=1,
                    text_score=0.6,
                    evidence=["합성 시험의 담당자 확인"],
                    scoring_version="synthetic-test",
                )
            )
        db.commit()
        return [item.id for item in complaints]


def create_data(ids: list[str]) -> dict[str, str]:
    return {
        "complaint_id": ids[0],
        "candidate_id": ids[1],
        "title": TITLE,
        "reason": REASON,
        "confirm": "yes",
    }


def command(client: TestClient, path: str, **data: str):
    return client.post(
        path,
        json={"reason": REASON, "confirm": "yes", **data},
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )


def create(client: TestClient, ids: list[str]) -> str:
    response = command(client, "/staff/incidents/create", **create_data(ids))
    assert response.status_code == 200, response.text
    return response.json()["redirect"].split("/")[-1]


def test_staff_flow_groups_counts_splits_and_preserves_individual_records(
    client: TestClient,
    test_app: FastAPI,
) -> None:
    ids = seed_pair(test_app)
    assert "표시할 사건이 없습니다" in client.get("/staff/incidents").text
    before = {item: client.get(f"/api/v1/complaints/{item}").json() for item in ids}
    prepare = client.get(f"/staff/incidents/prepare?complaint_id={ids[0]}&candidate_id={ids[1]}")
    assert prepare.status_code == 200
    assert "같은 현장 문제인가요?" in prepare.text
    assert "새 현장 사건 만들기" in prepare.text
    incident_id = create(client, ids)
    path = f"/staff/incidents/{incident_id}"
    detail = client.get(path)
    assert detail.status_code == 200 and "연결 민원 <strong>2건" in detail.text
    assert "data-no-publication" in detail.text and "긴급 신호 확인 필요" in detail.text
    assert "연결 민원 2건" in client.get("/staff/incidents").text
    assert incident_id in client.get(f"/complaints/{ids[0]}").text

    prepare = client.get(f"/staff/incidents/prepare?complaint_id={ids[2]}&candidate_id={ids[0]}")
    assert "기존 사건에 연결" in prepare.text
    linked = command(
        client, path + "/linked", revision="1", complaint_id=ids[2], candidate_id=ids[0]
    )
    assert linked.status_code == 200
    assert "연결 민원 3건" in client.get("/staff/incidents").text
    assert (
        command(client, path + "/status_changed", revision="2", status="resolved").status_code
        == 200
    )
    assert TITLE not in client.get("/staff/incidents").text
    assert TITLE in client.get("/staff/incidents?state=all").text
    split = command(client, path + "/unlinked", revision="3", complaint_id=ids[0])
    assert split.status_code == 200
    assert "연결 민원 2건" in client.get("/staff/incidents?state=all").text
    for item in ids:
        after = client.get(f"/api/v1/complaints/{item}").json()
        for key in ("status", "urgency", "assigned_department_id", "answer_draft", "created_at"):
            assert after[key] == before[item][key]
        assert any(event["action"].startswith("incident_") for event in after["audit_events"])
    with test_app.state.session_factory() as db:
        assert incidents.membership(db, ids[0]) is None
        events = list(db.scalars(select(IncidentEvent).order_by(IncidentEvent.revision)))
        assert [event.action for event in events] == [
            "created",
            "linked",
            "status_changed",
            "unlinked",
        ]
        assert events[-1].complaint_ids == sorted(ids[1:])


@pytest.mark.parametrize(
    "case",
    [
        "self",
        "suggested",
        "rejected",
        "welfare",
        "compensation",
        "category",
        "missing_location",
        "unknown",
    ],
)
def test_only_confirmed_eligible_pairs_can_be_grouped(
    client: TestClient,
    test_app: FastAPI,
    case: str,
) -> None:
    ids = seed_pair(test_app)
    data = create_data(ids)
    with test_app.state.session_factory() as db:
        first = db.get(Complaint, ids[0])
        assert first
        if case == "self":
            data["candidate_id"] = ids[0]
        elif case in {"suggested", "rejected"}:
            pair = db.scalar(
                select(DuplicateCandidate).where(DuplicateCandidate.complaint_id == ids[1])
            )
            assert pair
            pair.status = case
        elif case == "welfare":
            first.category = "welfare"
        elif case == "compensation":
            first.redacted_content += " 손해배상도 요구합니다."
        elif case == "category":
            first.category = "waste"
        elif case == "missing_location":
            first.redacted_location_text = None
        else:
            data["candidate_id"] = str(uuid4())
        db.commit()
    response = command(client, "/staff/incidents/create", **data)
    assert response.status_code in {400, 404}
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Incident)) == 0
        assert db.scalar(select(func.count()).select_from(ComplaintIncidentLink)) == 0
        assert db.scalar(select(func.count()).select_from(IncidentEvent)) == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "010-1111-2222"),
        ("reason", "합성 test@example.test"),
        ("confirm", ""),
        ("title", " "),
        ("reason", "짧음"),
        ("actor_id", "review.demo"),
    ],
)
def test_input_validation_and_no_unredacted_error_echo(
    client: TestClient,
    test_app: FastAPI,
    field: str,
    value: str,
) -> None:
    ids = seed_pair(test_app)
    data = {**create_data(ids), field: value}
    response = command(client, "/staff/incidents/create", **data)
    assert response.status_code in {400, 422}
    if field in {"title", "reason"} and ("@" in value or value.startswith("010-")):
        assert value not in response.text
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Incident)) == 0


def test_native_form_submission_csrf_and_error_text_retention(
    client: TestClient, test_app: FastAPI
) -> None:
    ids = seed_pair(test_app)
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/staff/incidents/create", data=create_data(ids)).status_code == 403
    data = {**create_data(ids), "csrf_token": csrf}
    invalid = client.post("/staff/incidents/create", data={**data, "confirm": ""})
    assert invalid.status_code == 422
    assert TITLE in invalid.text and REASON in invalid.text
    result = client.post("/staff/incidents/create", data=data, follow_redirects=False)
    assert result.status_code == 303
    assert client.get(result.headers["location"]).status_code == 200


def test_stale_revision_retries_completed_incident_and_cross_incident_link(
    client: TestClient,
    test_app: FastAPI,
) -> None:
    ids = seed_pair(test_app, 4)
    incident_id = create(client, ids)
    path = f"/staff/incidents/{incident_id}"
    assert command(client, "/staff/incidents/create", **create_data(ids)).status_code == 409
    assert (
        command(client, path + "/status_changed", revision="1", status="resolved").status_code
        == 200
    )
    assert (
        command(
            client, path + "/linked", revision="1", complaint_id=ids[2], candidate_id=ids[0]
        ).status_code
        == 409
    )
    assert (
        command(
            client, path + "/linked", revision="2", complaint_id=ids[2], candidate_id=ids[0]
        ).status_code
        == 400
    )
    assert command(client, path + "/unlinked", revision="2", complaint_id=ids[2]).status_code == 409
    assert (
        command(client, path + "/status_changed", revision="2", status="checking").status_code
        == 200
    )
    assert (
        command(
            client, path + "/linked", revision="3", complaint_id=ids[2], candidate_id=ids[0]
        ).status_code
        == 200
    )
    assert (
        command(
            client, path + "/linked", revision="3", complaint_id=ids[2], candidate_id=ids[0]
        ).status_code
        == 409
    )
    with test_app.state.session_factory() as db:
        incident = db.get(Incident, incident_id)
        assert incident and incident.revision == 4
        assert len(incidents.member_ids(db, incident_id)) == 3


def _login(client: TestClient, role: str) -> None:
    assert (
        client.post(
            "/login",
            data={"username": f"{role}.demo", "password": f"{role}-demo-2026"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    client.headers["X-CSRF-Token"] = client.get("/api/v1/session").json()["csrf_token"]


def test_roles_and_private_citizen_publication(client: TestClient, test_app: FastAPI) -> None:
    # Different browser sessions own the two synthetic intakes. Staff authentication alone
    # never grants citizen access to the second complaint or an anonymous incident listing.
    first, _, _ = submit(client, start(client))
    with TestClient(test_app) as second_owner:
        second, number, code = submit(second_owner, start(second_owner))
        pair = client.get(f"/api/v1/complaints/{second}/duplicate-candidates").json()
        assert pair[0]["candidate_complaint_id"] == first
        assert (
            client.post(
                f"/api/v1/complaints/{second}/duplicate-candidates/{first}/decision",
                json={"decision": "confirmed"},
            ).status_code
            == 200
        )
        incident_id = create(client, [first, second])
        path = f"/staff/incidents/{incident_id}"
        assert second_owner.get(path, follow_redirects=False).status_code == 303
        assert second_owner.get(f"/minwon/{first}").status_code == 404
        assert "data-citizen-incident" not in second_owner.get(f"/minwon/{second}").text
        _login(client, "triage")
        assert (
            command(client, path + "/published", revision="1", message=MESSAGE).status_code == 403
        )
        assert "공통 안내 공개</button>" not in client.get(path).text
        _login(client, "audit")
        assert (
            command(client, path + "/status_changed", revision="1", status="resolved").status_code
            == 403
        )
        assert "선택한 민원 분리</button>" not in client.get(path).text
        _login(client, "review")
        assert (
            command(
                client, path + "/published", revision="1", message="합성 연락 010-1111-2222"
            ).status_code
            == 400
        )
        assert (
            command(client, path + "/published", revision="1", message=MESSAGE).status_code == 200
        )
        public = client.get(f"/minwon/{first}")
        assert public.status_code == 200 and MESSAGE in public.text
        assert "현장 진행 안내" in public.text and "no-store" in public.headers["cache-control"]
        for private in (
            TITLE,
            incident_id,
            second,
            number,
            code,
            "review.demo",
            REASON,
            "연결 민원",
        ):
            assert private not in public.text
        assert client.get(f"/minwon/{second}").status_code == 404
        assert MESSAGE in second_owner.get(f"/minwon/{second}").text
        assert (
            command(
                client, path + "/status_changed", revision="2", status="in_progress"
            ).status_code
            == 200
        )
        assert MESSAGE not in client.get(f"/minwon/{first}").text
        assert (
            command(client, path + "/published", revision="2", message=MESSAGE).status_code == 409
        )
        assert (
            command(client, path + "/published", revision="3", message=MESSAGE).status_code == 200
        )
        assert command(client, path + "/withdrawn", revision="4").status_code == 200
        assert MESSAGE not in second_owner.get(f"/minwon/{second}").text
        assert (
            command(client, path + "/published", revision="5", message=MESSAGE).status_code == 200
        )
        assert (
            command(client, path + "/unlinked", revision="6", complaint_id=second).status_code
            == 200
        )
        assert MESSAGE not in second_owner.get(f"/minwon/{second}").text
        assert (
            command(client, path + "/published", revision="7", message=MESSAGE).status_code == 200
        )
        assert MESSAGE in client.get(f"/minwon/{first}").text
        assert MESSAGE not in second_owner.get(f"/minwon/{second}").text


def test_audit_failure_rolls_back_memberships_and_does_not_log_input(
    client: TestClient,
    test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ids = seed_pair(test_app)

    def fail(*args, **kwargs):
        raise RuntimeError("Never log this synthetic input: 010-1111-2222")

    monkeypatch.setattr(incidents, "record_audit", fail)
    result = command(client, "/staff/incidents/create", **create_data(ids))
    assert result.status_code == 503
    assert "010-1111-2222" not in result.text + caplog.text
    with test_app.state.session_factory() as db:
        for model in (Incident, ComplaintIncidentLink, IncidentEvent, AuditEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize(
    "statement", ["UPDATE incident_events SET reason = 'changed'", "DELETE FROM incident_events"]
)
def test_incident_event_history_is_append_only(
    client: TestClient, test_app: FastAPI, statement: str
) -> None:
    create(client, seed_pair(test_app))
    with test_app.state.session_factory() as db:
        with pytest.raises(IntegrityError, match="append-only"):
            db.execute(text(statement))
        db.rollback()
        assert db.scalar(select(func.count()).select_from(IncidentEvent)) == 1


@pytest.mark.parametrize("operation", ["create", "publish"])
def test_concurrent_commands_apply_once(
    client: TestClient, test_app: FastAPI, operation: str
) -> None:
    ids = seed_pair(test_app)
    incident_id = create(client, ids) if operation == "publish" else None
    action = "published" if incident_id else "created"
    data = (
        {"reason": REASON, "confirm": "yes", "message": MESSAGE, "revision": "1"}
        if incident_id
        else create_data(ids)
    )
    user = AuthenticatedUser("review.demo", "합성 검토자", "reviewer", "synthetic", 9999999999)
    barrier = Barrier(2, timeout=10)

    def run() -> int:
        with test_app.state.session_factory() as db:
            barrier.wait()
            try:
                incidents.execute(db, user, action, data, incident_id=incident_id)
                db.commit()
                return 200
            except incidents.IncidentError as exc:
                db.rollback()
                return exc.status

    with ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="Seongnam incident concurrency"
    ) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        assert sorted(future.result(timeout=20) for future in futures) == [200, 409]
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Incident)) == 1
        assert db.scalar(select(func.count()).select_from(ComplaintIncidentLink)) == 2
        assert db.scalar(select(func.count()).select_from(IncidentEvent)) == (
            2 if incident_id else 1
        )


def test_empty_group_publication_is_blocked_and_history_remains(
    client: TestClient, test_app: FastAPI
) -> None:
    ids = seed_pair(test_app)
    path = f"/staff/incidents/{create(client, ids)}"
    assert command(client, path + "/unlinked", revision="1", complaint_id=ids[0]).status_code == 200
    assert command(client, path + "/unlinked", revision="2", complaint_id=ids[1]).status_code == 200
    assert command(client, path + "/published", revision="3", message=MESSAGE).status_code == 400
    page = client.get(path)
    assert page.status_code == 200
    assert "연결된 민원이 없습니다" in page.text and "민원 분리" in page.text
    assert re.search(r'name="revision" value="3"', page.text)
    assert TITLE not in client.get("/staff/incidents").text
    assert TITLE in client.get("/staff/incidents?state=all").text
    assert (
        command(client, path + "/status_changed", revision="3", status="resolved").status_code
        == 400
    )


def test_new_membership_invalidates_publication_and_html_is_escaped(
    client: TestClient,
    test_app: FastAPI,
) -> None:
    ids = seed_pair(test_app)
    payload = {**create_data(ids), "title": "합성 <script>alert('test')</script> 사건"}
    created = command(client, "/staff/incidents/create", **payload)
    assert created.status_code == 200
    path = created.json()["redirect"]
    incident_id = path.split("/")[-1]
    message = "합성 <img src=x onerror=alert('test')> 진행 안내"
    assert command(client, path + "/published", revision="1", message=message).status_code == 200
    html = client.get(path).text
    assert "<script>alert('test')" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html and "&lt;img src=x" in html
    with test_app.state.session_factory() as db:
        progress = incidents.citizen_progress(db, ids[0])
        assert progress and set(progress) == {"status", "message", "published_at"}
        assert progress["message"] == message
    assert (
        command(
            client, path + "/linked", revision="2", complaint_id=ids[2], candidate_id=ids[0]
        ).status_code
        == 200
    )
    with test_app.state.session_factory() as db:
        for complaint_id in ids:
            assert incidents.citizen_progress(db, complaint_id) is None
        assert incidents.current_publication(db, incidents.get_incident(db, incident_id)) is None


def test_cross_group_and_malformed_command_paths_are_rejected(
    client: TestClient, test_app: FastAPI
) -> None:
    ids = seed_pair(test_app)
    other = seed_pair(test_app)
    path = f"/staff/incidents/{create(client, ids)}"
    other_path = f"/staff/incidents/{create(client, other)}"
    assert (
        command(client, path + "/unlinked", revision="1", complaint_id=other[0]).status_code == 409
    )
    assert (
        command(
            client, other_path + "/linked", revision="1", complaint_id=ids[2], candidate_id=ids[0]
        ).status_code
        == 409
    )
    assert command(client, path + "/created", **create_data(ids)).status_code == 400
    assert command(client, path + "/unsupported", revision="1").status_code == 400


def test_connection_count_limit_and_bounded_request_body(
    client: TestClient,
    test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = seed_pair(test_app)
    path = f"/staff/incidents/{create(client, ids)}"
    monkeypatch.setattr(incidents, "MAX_MEMBERS", 2)
    assert (
        command(
            client, path + "/linked", revision="1", complaint_id=ids[2], candidate_id=ids[0]
        ).status_code
        == 400
    )
    malformed = client.post(
        "/staff/incidents/create", content=b"x" * 32001, headers={"Accept": "application/json"}
    )
    assert malformed.status_code == 413
    invalid = client.post(
        "/staff/incidents/create", content=b"{", headers={"Accept": "application/json"}
    )
    assert invalid.status_code == 400 and invalid.json()["message"]
