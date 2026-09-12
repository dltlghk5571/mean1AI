"""Synthetic responses only; real HTTP, citizen disclosure and automatic links fail."""

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.incident_compare_schemas import ComparisonRequest, ComparisonResponse
from app.models import (
    AuditEvent,
    Complaint,
    ComplaintIncidentLink,
    DuplicateCandidate,
    Incident,
    IncidentComparison,
)
from app.services import incident_comparison
from app.services.incident_comparison import (
    ClubIncidentComparator,
    IncidentComparisonRunner,
    snapshot,
    validate_proposal,
)
from tests.test_club_planner import Stream
from tests.test_incidents import _login, seed_pair

SUMMARY = "합성 모델 제안: 두 기록의 현장 시설을 대조했습니다."


def test_published_json_schemas_match_contract() -> None:
    root = Path(__file__).resolve().parents[1] / "docs/contracts"
    for name, model in (("request", ComparisonRequest), ("response", ComparisonResponse)):
        published = json.loads(
            (root / f"incident-comparison-{name}.schema.json").read_text(encoding="utf-8")
        )
        assert published == model.model_json_schema()


def configuration(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        incident_compare_provider=overrides.pop("incident_compare_provider", "club"),
        incident_compare_endpoint_url=overrides.pop(
            "incident_compare_endpoint_url", "https://models.example.test/compare"
        ),
        incident_compare_model_id="synthetic-classifier-v1",
        incident_compare_api_key="synthetic-comparison-test-key",
        **overrides,
    )


def reply_for(request: httpx.Request, **overrides) -> httpx.Response:
    payload = json.loads(request.content)
    result = {
        "schema_version": "1",
        "request_id": payload["request_id"],
        "input_hash": payload["input_hash"],
        "model_id": payload["model_id"],
        "proposal": {
            "relation": "same_incident",
            "summary": SUMMARY,
            "evidence": [
                {"ref": ref, "field": "content", "quote": payload[ref]["content"][:30]}
                for ref in ("current", "candidate")
            ],
            "differences": [],
            "questions": ["시설 번호와 발생 시점이 같은가요?"],
        },
    }
    mutate = overrides.pop("mutate", None)
    if mutate:
        mutate(result)
    body = json.dumps(result, ensure_ascii=False).encode()
    return httpx.Response(
        overrides.pop("status", 200),
        headers=overrides.pop("headers", {"content-type": "application/json"}),
        stream=Stream(overrides.pop("body", body), delay=overrides.pop("delay", 0)),
    )


def setup_comparison(client: TestClient, test_app: FastAPI, handler=reply_for, *, provider="club"):
    ids = seed_pair(test_app)
    test_app.state.incident_comparator = IncidentComparisonRunner(
        configuration(incident_compare_provider=provider),
        test_app.state.session_factory,
        transport=httpx.MockTransport(handler),
    )
    return ids, f"/staff/incident-comparisons/{ids[1]}/{ids[0]}"


def submit_comparison(client: TestClient, path: str, *, expected_hash: str | None = None):
    page = client.get(path)
    assert page.status_code == 200
    match = re.search(r'name="expected_hash" value="([a-f0-9]{64})"', page.text)
    assert match
    return client.post(
        path, data={"expected_hash": expected_hash or match[1]}, follow_redirects=False
    )


def test_default_off_and_explicit_configuration() -> None:
    assert Settings(_env_file=None).incident_compare_provider == "off"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, incident_compare_provider="club")
    assert configuration(incident_compare_endpoint_url="http://127.0.0.1:9000/compare")


@pytest.mark.parametrize(
    "url",
    [
        "http://models.example.test",
        "https://user:secret@models.example.test",
        "https://models.example.test?key=secret",
        "https://models.example.test#fragment",
        "file:///private",
        "https://models.example.test:invalid/compare",
    ],
)
def test_comparison_reuses_secure_endpoint_boundary(url: str) -> None:
    with pytest.raises(ValidationError):
        configuration(incident_compare_endpoint_url=url)


def test_success_is_redacted_audited_and_never_confirms_or_links(
    client: TestClient, test_app: FastAPI
) -> None:
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return reply_for(request)

    ids, path = setup_comparison(client, test_app, handler)
    with test_app.state.session_factory() as db:
        current = db.get(Complaint, ids[1])
        assert current
        current.redacted_content += " 합성 연락처 010-1111-2222 test@example.test"
        pair = db.scalar(
            select(DuplicateCandidate).where(DuplicateCandidate.complaint_id == ids[1])
        )
        assert pair
        pair.status = "suggested"
        db.commit()
    before = {item: client.get(f"/api/v1/complaints/{item}").json() for item in ids[:2]}
    response = submit_comparison(client, path)
    assert response.status_code == 303
    assert len(requests) == 1
    outgoing = requests[0]
    assert outgoing.headers["authorization"] == "Bearer synthetic-comparison-test-key"
    for private in ("010-1111-2222", "test@example.test", *ids):
        assert private not in outgoing.content.decode()
    public = client.get(path)
    assert SUMMARY in public.text and "같은 사건 가능성" in public.text
    assert "no-store" in public.headers["cache-control"]
    with test_app.state.session_factory() as db:
        record = db.scalar(select(IncidentComparison))
        assert record and record.status == "ready" and record.result
        assert record.result["relation"] == "same_incident"
        assert db.scalar(select(func.count()).select_from(Incident)) == 0
        assert db.scalar(select(func.count()).select_from(ComplaintIncidentLink)) == 0
        assert (
            db.scalar(
                select(DuplicateCandidate.status).where(DuplicateCandidate.complaint_id == ids[1])
            )
            == "suggested"
        )
        events = list(db.scalars(select(AuditEvent)))
        assert len(events) == 4
        assert {event.action for event in events} == {
            "incident_comparison_requested",
            "incident_comparison_ready",
        }
        assert SUMMARY not in repr([event.details for event in events])
        assert all(event.details["automatic_link"] is False for event in events)
    for item, record in before.items():
        after = client.get(f"/api/v1/complaints/{item}").json()
        for field in ("status", "urgency", "assigned_department_id", "answer_draft"):
            assert after[field] == record[field]


def test_off_demo_roles_and_citizen_boundary(client: TestClient, test_app: FastAPI) -> None:
    ids, path = setup_comparison(client, test_app, provider="off")
    assert "비교 모델이 아직 연결되지 않았습니다" in client.get(path).text
    assert submit_comparison(client, path).status_code == 503
    test_app.state.incident_comparator = IncidentComparisonRunner(
        configuration(incident_compare_provider="demo"),
        test_app.state.session_factory,
    )
    assert submit_comparison(client, path).status_code == 303
    result = client.get(path)
    assert "합성 응답 시연" in result.text and "판단 보류" in result.text
    csrf = client.headers.pop("X-CSRF-Token")
    assert submit_comparison(client, path).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    _login(client, "audit")
    assert client.get(path).status_code == 200
    assert "합성 비교 응답 확인</button>" not in client.get(path).text
    assert (
        client.post(path, data={"expected_hash": "a" * 64}, follow_redirects=False).status_code
        == 403
    )
    with TestClient(test_app) as outsider:
        assert outsider.get(path, follow_redirects=False).status_code == 303
        assert outsider.get(f"/minwon/{ids[0]}").status_code == 404


def _request(test_app: FastAPI, ids: list[str]) -> ComparisonRequest:
    with test_app.state.session_factory() as db:
        data = snapshot(db, ids[1], ids[0])
    return ComparisonRequest(
        request_id=uuid4(),
        model_id="synthetic-classifier-v1",
        input_hash=data["input_hash"],
        current=data["current"],
        candidate=data["candidate"],
    )


@pytest.mark.parametrize(
    "problem",
    [
        "request",
        "hash",
        "model",
        "quote",
        "foreign_ref",
        "extra",
        "pii",
        "length",
        "missing_source",
        "redirect",
        "html",
        "status",
        "size",
        "compressed",
        "synthetic",
    ],
)
def test_untrusted_responses_are_rejected(
    client: TestClient, test_app: FastAPI, problem: str
) -> None:
    count = 0

    def handler(request):
        nonlocal count
        count += 1

        def mutate(result):
            if problem == "request":
                result["request_id"] = str(uuid4())
            elif problem == "hash":
                result["input_hash"] = "b" * 64
            elif problem == "model":
                result["model_id"] = "wrong-model"
            elif problem == "quote":
                result["proposal"]["evidence"][0]["quote"] = "입력에 없는 합성 시설"
            elif problem == "foreign_ref":
                result["proposal"]["evidence"][0]["ref"] = "another-citizen"
            elif problem == "extra":
                result["proposal"]["action"] = "link_and_close"
            elif problem == "pii":
                result["proposal"]["summary"] = "합성 전화번호 010-1111-2222"
            elif problem == "length":
                result["proposal"]["questions"] = ["가" * 181]
            elif problem == "missing_source":
                result["proposal"]["evidence"] = result["proposal"]["evidence"][:1]

        options: dict[str, dict[str, Any]] = {
            "redirect": {"status": 307, "headers": {"location": "https://elsewhere.example.test"}},
            "html": {"headers": {"content-type": "text/html"}},
            "status": {"status": 500},
            "size": {"body": b"x" * 12001},
            "compressed": {
                "headers": {"content-type": "application/json", "content-encoding": "gzip"}
            },
            "synthetic": {
                "headers": {"content-type": "application/json", "x-model-execution": "synthetic"}
            },
        }
        return reply_for(request, mutate=mutate, **options.get(problem, {}))

    _, path = setup_comparison(client, test_app, handler)
    assert submit_comparison(client, path).status_code == 303
    html = client.get(path).text
    assert "모델 응답을 검증하지 못했습니다" in html and SUMMARY not in html
    assert "010-1111-2222" not in html
    assert count == 1
    with test_app.state.session_factory() as db:
        record = db.scalar(select(IncidentComparison))
        assert record and record.status == "failed" and record.result is None
        assert db.scalar(select(func.count()).select_from(Incident)) == 0


def test_total_timeout_closes_stream_and_transport_rejects_identifiers(
    client: TestClient, test_app: FastAPI
) -> None:
    ids = seed_pair(test_app)
    payload = _request(test_app, ids)
    stream = Stream(b"{}", delay=5)
    settings = configuration().model_copy(update={"incident_compare_timeout_seconds": 0.02})
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "application/json"}, stream=stream
        )
    )
    model = ClubIncidentComparator(settings, transport=transport)
    with pytest.raises(ValueError, match="^incident_comparison_model_failed$"):
        model.compare(payload)
    assert stream.closed
    payload.current.content = "합성 연락처 010-1111-2222"
    called = False

    def forbidden(request):
        nonlocal called
        called = True
        return reply_for(request)

    with pytest.raises(ValueError):
        ClubIncidentComparator(configuration(), transport=httpx.MockTransport(forbidden)).compare(
            payload
        )
    assert called is False


def test_opaque_hash_digits_do_not_trigger_prose_redaction(
    client: TestClient, test_app: FastAPI
) -> None:
    payload = _request(test_app, seed_pair(test_app))
    payload.input_hash = "a01011112222b".ljust(64, "c")
    model = ClubIncidentComparator(configuration(), transport=httpx.MockTransport(reply_for))
    response = model.compare(payload)
    assert response.input_hash == payload.input_hash


@pytest.mark.parametrize("change", ["body", "rejected", "field_status"])
def test_changes_during_model_request_discard_result(
    client: TestClient, test_app: FastAPI, change: str
) -> None:
    ids: list[str] = []

    def handler(request):
        with test_app.state.session_factory() as db:
            if change == "body":
                item = db.get(Complaint, ids[1])
                assert item
                item.redacted_content += " 새롭게 확인한 합성 내용입니다."
            elif change == "rejected":
                pair = db.scalar(
                    select(DuplicateCandidate).where(DuplicateCandidate.complaint_id == ids[1])
                )
                assert pair
                pair.status = "rejected"
            else:
                incident = Incident(
                    title="합성 조치 기록", category="streetlight", status="resolved", revision=1
                )
                db.add(incident)
                db.flush()
                db.add(ComplaintIncidentLink(complaint_id=ids[0], incident_id=incident.id))
            db.commit()
        return reply_for(request)

    ids, path = setup_comparison(client, test_app, handler)
    assert submit_comparison(client, path).status_code == 303
    with test_app.state.session_factory() as db:
        record = db.scalar(select(IncidentComparison))
        assert record and record.status == "stale" and record.result is None
    assert SUMMARY not in client.get(path).text


def test_old_page_policy_and_capacity_do_not_call_model(
    client: TestClient, test_app: FastAPI
) -> None:
    def forbidden(request):
        pytest.fail("This request must not call a model")

    ids, path = setup_comparison(client, test_app, forbidden)
    assert submit_comparison(client, path, expected_hash="0" * 64).status_code == 409
    runner = test_app.state.incident_comparator
    for _ in range(runner.settings.incident_compare_max_concurrent):
        assert runner.capacity.acquire(blocking=False)
    try:
        assert submit_comparison(client, path).status_code == 429
    finally:
        for _ in range(runner.settings.incident_compare_max_concurrent):
            runner.capacity.release()
    with test_app.state.session_factory() as db:
        item = db.get(Complaint, ids[0])
        assert item
        item.redacted_content += " 손해배상을 요구합니다."
        db.commit()
        payload = snapshot(db, ids[1], ids[0])
    assert (
        client.post(
            path, data={"expected_hash": payload["input_hash"]}, follow_redirects=False
        ).status_code
        == 400
    )
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(IncidentComparison)) == 0


@pytest.mark.parametrize("guard", ["truncated_input", "completed_field_task"])
def test_incomplete_or_completed_context_holds_same_incident_proposal(
    client: TestClient, test_app: FastAPI, guard: str
) -> None:
    ids = seed_pair(test_app)
    payload = _request(test_app, ids)
    if guard == "truncated_input":
        payload.current.content_truncated = True
    else:
        payload.candidate.field_status = "resolved"
    model = ClubIncidentComparator(configuration(), transport=httpx.MockTransport(reply_for))
    result = validate_proposal(payload, model.compare(payload))
    assert result["relation"] == "uncertain" and guard in result["guards"]


@pytest.mark.parametrize("stage", ["requested", "ready"])
def test_audit_failures_withhold_model_access_or_result(
    client: TestClient,
    test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stage: str,
) -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return reply_for(request)

    _, path = setup_comparison(client, test_app, handler)
    original = incident_comparison.record_audit

    def fail(db, **kwargs):
        if kwargs["action"] == "incident_comparison_" + stage:
            raise RuntimeError("Sensitive synthetic failure 010-1111-2222")
        return original(db, **kwargs)

    monkeypatch.setattr(incident_comparison, "record_audit", fail)
    response = submit_comparison(client, path)
    assert response.status_code == 503
    assert SUMMARY not in response.text and "010-1111-2222" not in response.text + caplog.text
    assert calls == (0 if stage == "requested" else 1)
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(IncidentComparison)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == (
            0 if stage == "requested" else 2
        )
    assert test_app.state.incident_comparator.capacity.acquire(blocking=False)
    test_app.state.incident_comparator.capacity.release()


def test_saved_results_are_immutable_and_hidden_after_context_change(
    client: TestClient, test_app: FastAPI
) -> None:
    ids, path = setup_comparison(client, test_app)
    assert submit_comparison(client, path).status_code == 303
    with test_app.state.session_factory() as db:
        for statement in (
            "UPDATE incident_comparisons SET status='failed'",
            "DELETE FROM incident_comparisons",
        ):
            with pytest.raises(IntegrityError, match="append-only"):
                db.execute(text(statement))
            db.rollback()
        item = db.get(Complaint, ids[1])
        assert item
        item.redacted_content += " 확인 내용 변경"
        db.commit()
    page = client.get(path)
    assert page.status_code == 200 and "이전 결과를 숨겼습니다" in page.text
    assert SUMMARY not in page.text
