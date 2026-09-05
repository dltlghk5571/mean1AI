import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import Base, make_engine, make_session_factory
from app.main import create_app
from app.models import AIProcessingJob, AIProcessingRequest, AuditEvent, Complaint
from app.schemas import (
    ClassificationCandidate,
    ClassificationResult,
    GroundedDraftSentence,
    StructuredDraftOutput,
    Urgency,
)
from app.services import ai_queue
from app.services.classifier import ClassifierError
from app.services.pipeline import ComplaintPipeline
from app.services.runtime import build_pipeline
from app.worker import main, run_once


@dataclass
class Clock:
    time: datetime = datetime(2026, 9, 5, 12, tzinfo=UTC)

    def __call__(self, *args: object) -> datetime:
        return self.time

    def advance(self, seconds: int) -> None:
        self.time += timedelta(seconds=seconds)


@dataclass
class QueueHarness:
    client: TestClient
    pipeline: ComplaintPipeline
    factory: sessionmaker[Session]
    classifier: Mock
    clock: Clock

    def create(self, **overrides) -> dict:
        payload = {
            "title": "합성 가로등 고장 010-1234-5678",
            "content": "합성 시험동 가로등 불이 꺼졌습니다. synthetic@example.com",
            "location_text": "합성 시험동 입구 010-9876-5432",
            "channel": "web",
        }
        payload.update(overrides)
        response = self.client.post("/api/v1/complaints", json=payload)
        assert response.status_code == 201
        return response.json()

    def detail(self, complaint_id: str) -> dict:
        response = self.client.get(f"/api/v1/complaints/{complaint_id}")
        assert response.status_code == 200
        return response.json()

    def run(self):
        return run_once(self.factory, self.pipeline, clock=self.clock)


@pytest.fixture
def queue(client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> QueueHarness:
    pipeline = test_app.state.pipeline
    pipeline.settings = pipeline.settings.model_copy(
        update={
            "ai_provider": "openai",
            "ai_deferred_enabled": True,
            "openai_api_key": SecretStr("synthetic-unused-key"),
            "ai_queue_retry_seconds": 5,
        }
    )
    result = ClassificationResult(
        category="streetlight",
        subcategory="가로등·보안등 고장",
        candidates=[
            ClassificationCandidate(
                department_id="ROAD_LIGHTING", confidence=0.99, reason="합성 가로등 점등 불량"
            )
        ],
        evidence_summary="합성 점등 불량",
        provider="openai",
    )
    classifier = Mock(provider_name="openai", classify=Mock(return_value=result))
    pipeline.classifier = classifier
    clock = Clock()
    monkeypatch.setattr("app.services.pipeline.datetime", Mock(now=clock))
    return QueueHarness(client, pipeline, test_app.state.session_factory, classifier, clock)


def _queue_events(detail: dict) -> list[dict]:
    return [event for event in detail["audit_events"] if event["action"].startswith("ai_job_")]


def test_rules_stay_synchronous_even_when_deferred_flag_is_enabled(
    client: TestClient, test_app: FastAPI
) -> None:
    pipeline = test_app.state.pipeline
    pipeline.settings.ai_deferred_enabled = True
    response = client.post(
        "/api/v1/complaints",
        json={
            "title": "합성 가로등 고장",
            "content": "가로등 불이 꺼졌습니다.",
            "location_text": "합성 시험동",
        },
    )
    assert response.status_code == 201
    assert response.json()["ai_processing"] is None
    assert response.json()["status"] == "assigned"
    assert run_once(test_app.state.session_factory, pipeline) is None
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AIProcessingJob)) == 0


def test_enqueue_is_durable_redacted_and_completion_still_requires_review(
    queue: QueueHarness,
) -> None:
    created = queue.create()
    job = created["ai_processing"]
    assert job["state"] == "queued"
    assert job["attempts"] == 0
    assert created["status"] == "needs_review"
    assert created["requires_human_review"] is True
    assert created["assigned_department_id"] is None
    assert created["answer_draft"]
    queue.classifier.classify.assert_not_called()
    assert "claim_token" not in job
    assert "input_sha256" not in job
    assert not {"title", "content", "prompt", "result", "answer_draft"} & set(
        AIProcessingJob.__table__.columns.keys()
    )
    before = queue.detail(created["id"])
    assert _queue_events(before)[0]["actor_id"] == "review.demo"
    # A fresh engine/session can resume work persisted by the web process.
    other_engine = make_engine(queue.pipeline.settings.database_url)
    try:
        outcome = run_once(make_session_factory(other_engine), queue.pipeline, clock=queue.clock)
    finally:
        other_engine.dispose()
    assert outcome is not None and outcome.state == "completed"
    detail = queue.detail(created["id"])
    assert detail["ai_processing"]["state"] == "completed"
    assert detail["ai_processing"]["attempts"] == 1
    assert detail["requires_human_review"] is True
    assert detail["assigned_department_id"] is None
    assert detail["status"] == "needs_review"
    assert detail["reviewed_at"] is None
    assert queue.client.get(f"/api/v1/complaints/{created['id']}/reviews").json() == []
    assert [event["action"] for event in _queue_events(detail)] == [
        "ai_job_enqueued",
        "ai_job_claimed",
        "ai_job_completed",
    ]
    assert queue.run() is None
    queue.classifier.classify.assert_called_once()
    provider_input = json.dumps(queue.classifier.classify.call_args.kwargs, ensure_ascii=False)
    for identifier in ("010-1234-5678", "synthetic@example.com", "010-9876-5432"):
        assert identifier not in provider_input
        assert identifier not in json.dumps(detail, ensure_ascii=False)
    for field in (created["redacted_title"], created["redacted_content"], created["answer_draft"]):
        assert field not in json.dumps(detail["audit_events"], ensure_ascii=False)


def test_reprocess_is_idempotent_for_active_and_completed_requests(queue: QueueHarness) -> None:
    created = queue.create()
    path = f"/api/v1/complaints/{created['id']}/reprocess"
    assert queue.client.post(path).json()["ai_processing"]["id"] == created["ai_processing"]["id"]
    assert len(_queue_events(queue.detail(created["id"]))) == 1
    queue.run()
    key = str(uuid4())
    first = queue.client.post(path, headers={"Idempotency-Key": key})
    second = queue.client.post(path, headers={"Idempotency-Key": key})
    assert first.status_code == second.status_code == 200
    assert first.json()["ai_processing"]["id"] != created["ai_processing"]["id"]
    assert first.json()["ai_processing"] == second.json()["ai_processing"]
    queue.run()
    before = queue.detail(created["id"])
    assert queue.client.post(path, headers={"Idempotency-Key": key}).status_code == 200
    assert queue.detail(created["id"]) == before
    history = queue.client.get(f"/api/v1/complaints/{created['id']}/ai-processing").json()
    assert [job["state"] for job in history] == ["completed", "completed"]
    assert queue.classifier.classify.call_count == 2


def test_two_workers_claim_a_job_only_once(queue: QueueHarness) -> None:
    created = queue.create()
    barrier = Barrier(2)

    def claim():
        with queue.factory() as db:
            barrier.wait(timeout=5)
            result = ai_queue.claim_next(db, now=queue.clock(), lease_seconds=120)
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="seongnam-queue-claim") as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(result is not None for result in results) == 1
    detail = queue.detail(created["id"])
    assert detail["ai_processing"]["state"] == "processing"
    assert detail["ai_processing"]["attempts"] == 1
    assert [event["action"] for event in _queue_events(detail)] == [
        "ai_job_enqueued",
        "ai_job_claimed",
    ]


def test_retries_are_bounded_and_errors_are_body_free(
    queue: QueueHarness, caplog: pytest.LogCaptureFixture
) -> None:
    marker = "SYNTHETIC_PRIVATE_EXCEPTION 010-1111-2222 synthetic-error@example.com"
    queue.classifier.classify.side_effect = ClassifierError(marker)
    created = queue.create()
    first = queue.run()
    assert first is not None and first.state == "queued"
    assert queue.run() is None
    queue.clock.advance(5)
    second = queue.run()
    assert second is not None and second.state == "queued"
    queue.clock.advance(9)
    assert queue.run() is None
    queue.clock.advance(1)
    third = queue.run()
    assert third is not None and third.state == "failed"
    queue.clock.advance(10_000)
    assert queue.run() is None
    detail = queue.detail(created["id"])
    job = detail["ai_processing"]
    assert job["attempts"] == job["max_attempts"] == 3
    assert job["last_error_code"] == "provider_error"
    assert job["finished_at"] is not None
    assert detail["requires_human_review"] is True
    assert detail["status"] == "needs_review"
    assert detail["assigned_department_id"] is None
    assert detail["answer_draft"] == created["answer_draft"]
    assert marker not in json.dumps(detail)
    assert marker not in caplog.text
    assert queue.classifier.classify.call_count == 3
    assert [event["action"] for event in _queue_events(detail)] == [
        "ai_job_enqueued",
        "ai_job_claimed",
        "ai_job_attempt_failed",
        "ai_job_claimed",
        "ai_job_attempt_failed",
        "ai_job_claimed",
        "ai_job_attempt_failed",
        "ai_job_failed",
    ]


def test_expired_claim_recovery_and_stale_result_rejection(queue: QueueHarness) -> None:
    created = queue.create()
    with queue.factory() as db:
        first = ai_queue.claim_next(db, now=queue.clock(), lease_seconds=120)
        db.commit()
    assert first is not None
    queue.clock.advance(120)
    assert queue.run() is None  # Recovery schedules a delayed retry, with no extra call.
    queue.classifier.classify.assert_not_called()
    with queue.factory() as db:
        assert not ai_queue.complete(db, first, now=queue.clock())
        assert not ai_queue.fail(db, first, now=queue.clock(), error_code="provider_error")
        db.commit()
    queue.clock.advance(5)
    outcome = queue.run()
    assert outcome is not None and outcome.state == "completed"
    detail = queue.detail(created["id"])
    assert detail["ai_processing"]["attempts"] == 2
    failures = [
        event for event in _queue_events(detail) if event["action"] == "ai_job_attempt_failed"
    ]
    assert failures[0]["details"]["reason"] == "lease_expired"


def test_expired_last_attempt_falls_back_without_running_provider(queue: QueueHarness) -> None:
    queue.pipeline.settings.ai_queue_max_attempts = 1
    created = queue.create()
    with queue.factory() as db:
        assert ai_queue.claim_next(db, now=queue.clock(), lease_seconds=120)
        db.commit()
    queue.clock.advance(120)
    assert queue.run() is None
    assert queue.detail(created["id"])["ai_processing"]["state"] == "failed"
    queue.classifier.classify.assert_not_called()


@pytest.mark.parametrize("during_provider", [False, True])
def test_human_approval_supersedes_pending_and_inflight_results(
    queue: QueueHarness, during_provider: bool
) -> None:
    created = queue.create()

    def approve(**kwargs):
        response = queue.client.post(
            f"/api/v1/complaints/{created['id']}/approve",
            json={
                "department_id": "ROAD_LIGHTING",
                "answer_draft": "담당자가 직접 확인한 합성 답변입니다.",
            },
        )
        assert response.status_code == 200
        return queue.classifier.classify.return_value

    if during_provider:
        queue.classifier.classify.side_effect = approve
        result = queue.run()
        assert result is not None and result.state == "discarded"
    else:
        approve()
        assert queue.run() is None
        queue.classifier.classify.assert_not_called()
    detail = queue.detail(created["id"])
    assert detail["status"] == "reviewed"
    assert detail["reviewed_by"] == "review.demo"
    assert detail["answer_draft"] == "담당자가 직접 확인한 합성 답변입니다."
    assert detail["ai_processing"]["last_error_code"] == "human_review_superseded"
    assert detail["ai_processing"]["state"] == "failed"
    assert _queue_events(detail)[-1]["actor_id"] == "review.demo"
    assert _queue_events(detail)[-1]["actor_type"] == "officer"
    assert len(queue.client.get(f"/api/v1/complaints/{created['id']}/reviews").json()) == 1
    assert not any(event["action"] == "ai_job_completed" for event in _queue_events(detail))


@pytest.mark.parametrize(
    ("content", "status"),
    [
        ("합성 가스 누출과 가로등 고장을 신고합니다.", "urgent_review"),
        ("합성 지원 대상 자격을 자동 결정해 주세요.", "needs_review"),
        ("합성 아동학대 신고가 필요합니다.", "needs_review"),
        ("합성 과태료 처분을 취소해 주세요.", "needs_review"),
    ],
)
def test_urgent_and_sensitive_intake_bypasses_expensive_work(
    queue: QueueHarness, content: str, status: str
) -> None:
    created = queue.create(content=content)
    assert created["ai_processing"] is None
    assert created["status"] == status
    assert created["requires_human_review"] is True
    assert created["assigned_department_id"] is None
    assert _queue_events(queue.detail(created["id"]))[-1]["action"] == "ai_job_skipped"
    assert queue.run() is None
    queue.classifier.classify.assert_not_called()


def test_provider_urgency_and_untrusted_output_cannot_bypass_safety(queue: QueueHarness) -> None:
    queue.classifier.classify.return_value = queue.classifier.classify.return_value.model_copy(
        update={
            "urgency": Urgency.CRITICAL,
            "review_reasons": ["RAW_PROVIDER_BODY synthetic@example.com"],
            "evidence_summary": "합성 010-1234-5678",
        }
    )
    created = queue.create()
    assert queue.run().state == "completed"
    detail = queue.detail(created["id"])
    assert detail["status"] == "urgent_review"
    assert detail["assigned_department_id"] is None
    assert "RAW_PROVIDER_BODY" not in json.dumps(detail)
    assert "010-1234-5678" not in json.dumps(detail)


def test_draft_failure_rolls_back_and_retries(
    queue: QueueHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = queue.create()
    monkeypatch.setattr(
        queue.pipeline.drafter, "generate", Mock(side_effect=RuntimeError("private body"))
    )
    result = queue.run()
    assert result is not None and result.state == "queued"
    detail = queue.detail(created["id"])
    assert detail["answer_draft"] == created["answer_draft"]
    assert detail["ai_processing"]["last_error_code"] == "processing_error"
    assert sum(event["action"] == "triage_completed" for event in detail["audit_events"]) == 1


def test_result_and_completion_audit_roll_back_together(
    queue: QueueHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = queue.create()
    original = queue.pipeline.apply_deferred

    def broken_apply(db, complaint, prepared):
        original(db, complaint, prepared)
        db.flush()
        raise RuntimeError("synthetic failure before commit")

    monkeypatch.setattr(queue.pipeline, "apply_deferred", broken_apply)
    assert queue.run().state == "queued"
    detail = queue.detail(created["id"])
    assert detail["answer_draft"] == created["answer_draft"]
    assert not any(event["action"] == "ai_job_completed" for event in _queue_events(detail))
    assert sum(event["action"] == "triage_completed" for event in detail["audit_events"]) == 1


def test_queue_events_are_append_only(queue: QueueHarness) -> None:
    created = queue.create()
    with queue.factory() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "ai_job_enqueued"))
        assert event is not None
        event.details = {"rewritten": True}
        with pytest.raises(IntegrityError, match="append-only"):
            db.commit()
        db.rollback()
        db.delete(event)
        with pytest.raises(IntegrityError, match="append-only"):
            db.commit()
    assert len(_queue_events(queue.detail(created["id"]))) == 1


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///:memory:",
        "sqlite://",
        "postgresql://localhost/demo",
        "sqlite:///file:mem?mode=memory",
    ],
)
def test_queue_configuration_requires_local_durable_storage(database_url: str) -> None:
    with pytest.raises(ValueError, match="local file-backed SQLite"):
        ai_queue.validate_local_queue(Settings(database_url=database_url))


@pytest.mark.parametrize("api_key", [None, SecretStr(""), SecretStr("   ")])
def test_deferred_configuration_fails_closed_without_key_or_in_production(
    api_key: SecretStr | None,
) -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_pipeline(
            Settings(ai_provider="openai", ai_deferred_enabled=True, openai_api_key=api_key)
        )
    with pytest.raises(ValueError, match="local file-backed SQLite"):
        ai_queue.validate_local_queue(Settings(app_env="production"))


def _login_as(client: TestClient, role: str, *, csrf: bool = True) -> None:
    account = {"reviewer": "review", "triage_officer": "triage", "auditor": "audit"}[role]
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    response = client.post(
        "/login",
        data={"username": f"{account}.demo", "password": f"{account}-demo-2026"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    if csrf:
        client.headers["X-CSRF-Token"] = client.get("/api/v1/session").json()["csrf_token"]


@pytest.mark.parametrize("role", ["reviewer", "triage_officer", "auditor", "anonymous"])
def test_queue_authorization_and_worker_operations_are_not_http_actions(
    queue: QueueHarness, role: str
) -> None:
    created = queue.create()
    queue.run()
    before = queue.detail(created["id"])
    if role == "anonymous":
        queue.client.cookies.clear()
        queue.client.headers.pop("X-CSRF-Token", None)
    else:
        _login_as(queue.client, role)
    path = f"/api/v1/complaints/{created['id']}"
    history = queue.client.get(path + "/ai-processing")
    assert history.status_code == (401 if role == "anonymous" else 200)
    if role != "anonymous":
        assert "claim_token" not in history.text
        assert queue.client.post(path + "/ai-processing").status_code == 405
        for action in ("claim", "complete", "fail"):
            assert queue.client.post(path + f"/ai-processing/{action}").status_code == 404
    response = queue.client.post(path + "/reprocess", headers={"Idempotency-Key": str(uuid4())})
    expected = 401 if role == "anonymous" else 403 if role == "auditor" else 200
    assert response.status_code == expected
    if role in {"reviewer", "triage_officer"}:
        detail = queue.detail(created["id"])
        event = _queue_events(detail)[-1]
        assert event["action"] == "ai_job_enqueued"
        assert event["actor_id"] == ("review.demo" if role == "reviewer" else "triage.demo")
    else:
        _login_as(queue.client, "reviewer")
        assert queue.detail(created["id"]) == before


@pytest.mark.parametrize("form", [False, True])
@pytest.mark.parametrize("role", ["reviewer", "triage_officer", "auditor"])
def test_enqueue_requires_csrf_without_any_state_or_audit_change(
    queue: QueueHarness, form: bool, role: str
) -> None:
    created = queue.create()
    queue.run()
    before = queue.detail(created["id"])
    _login_as(queue.client, role, csrf=False)
    path = (
        f"/complaints/{created['id']}/reprocess"
        if form
        else f"/api/v1/complaints/{created['id']}/reprocess"
    )
    assert queue.client.post(path).status_code == 403
    assert queue.detail(created["id"]) == before


def test_ui_and_list_api_distinguish_all_four_ai_states(queue: QueueHarness) -> None:
    created = queue.create()

    def check_state(state: str, label: str) -> None:
        for path in ("/staff", f"/complaints/{created['id']}"):
            response = queue.client.get(path)
            assert response.status_code == 200
            assert f'data-ai-state="{state}"' in response.text
            assert label in response.text
        listing = queue.client.get("/api/v1/complaints").json()
        assert listing[0]["ai_processing"]["state"] == state

    check_state("queued", "AI 분석 대기")
    with queue.factory() as db:
        claim = ai_queue.claim_next(db, now=queue.clock(), lease_seconds=120)
        db.commit()
    assert claim is not None
    check_state("processing", "AI 분석 중")
    with queue.factory() as db:
        assert ai_queue.fail(
            db, claim, now=queue.clock(), error_code="provider_error", retryable=False
        )
        db.commit()
    check_state("failed", "AI 분석 실패 · 사람 검토")
    assert queue.client.post(f"/api/v1/complaints/{created['id']}/reprocess").status_code == 200
    queue.run()
    check_state("completed", "AI 분석 완료")
    _login_as(queue.client, "auditor")
    page = queue.client.get(f"/complaints/{created['id']}").text
    assert 'data-ai-state="completed"' in page
    assert "data-reprocess-form" not in page
    assert 'id="review-form"' not in page


@pytest.mark.parametrize("during_provider", [False, True])
@pytest.mark.parametrize("change", ["catalog", "input", "configuration"])
def test_stale_work_fails_closed_before_or_after_provider(
    queue: QueueHarness, monkeypatch: pytest.MonkeyPatch, change: str, during_provider: bool
) -> None:
    created = queue.create()

    def invalidate(**kwargs):
        if change == "catalog":
            monkeypatch.setattr(
                "app.worker.ensure_current_catalog",
                Mock(side_effect=ValueError("catalog_superseded")),
            )
        elif change == "configuration":
            queue.pipeline.settings.openai_model = "synthetic-different-model"
        else:
            with queue.factory() as db:
                complaint = db.get(Complaint, created["id"])
                assert complaint is not None
                complaint.content = "합성 원본이 다른 민원으로 변경되었습니다."
                db.commit()
        return queue.classifier.classify.return_value

    if during_provider:
        queue.classifier.classify.side_effect = invalidate
    else:
        invalidate()
    outcome = queue.run()
    assert outcome is not None and outcome.state == "failed"
    detail = queue.detail(created["id"])
    assert detail["status"] == "needs_review"
    assert detail["assigned_department_id"] is None
    assert detail["ai_processing"]["last_error_code"] == f"{change}_changed"
    assert detail["answer_draft"] == created["answer_draft"]
    assert queue.classifier.classify.call_count == int(during_provider)


def test_transient_failure_then_success_has_single_committed_result(queue: QueueHarness) -> None:
    queue.classifier.classify.side_effect = [
        ClassifierError("synthetic transient"),
        queue.classifier.classify.return_value,
    ]
    created = queue.create()
    assert queue.run().state == "queued"
    queue.clock.advance(5)
    assert queue.run().state == "completed"
    before = queue.detail(created["id"])
    assert before["ai_processing"]["last_error_code"] is None
    assert before["ai_processing"]["attempts"] == 2
    assert queue.run() is None
    assert queue.detail(created["id"]) == before
    assert sum(event["action"] == "ai_job_completed" for event in _queue_events(before)) == 1


def test_enqueue_and_preflight_rollback_as_one_transaction(
    queue: QueueHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ai_queue.enqueue

    def broken_enqueue(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic failure before intake commit")

    monkeypatch.setattr(ai_queue, "enqueue", broken_enqueue)
    with pytest.raises(RuntimeError, match="before intake commit"):
        queue.create()
    with queue.factory() as db:
        for model in (Complaint, AIProcessingJob, AIProcessingRequest, AuditEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_provider_returning_after_lease_expiry_cannot_publish(queue: QueueHarness) -> None:
    created = queue.create()

    def slow_provider(**kwargs):
        queue.clock.advance(120)
        return queue.classifier.classify.return_value

    queue.classifier.classify.side_effect = slow_provider
    assert queue.run().state == "discarded"
    detail = queue.detail(created["id"])
    assert detail["ai_processing"]["state"] == "processing"
    assert detail["answer_draft"] == created["answer_draft"]
    assert queue.run() is None
    assert queue.detail(created["id"])["ai_processing"]["state"] == "queued"


def test_expensive_provider_is_not_run_in_rules_worker_mode(queue: QueueHarness) -> None:
    created = queue.create()
    queue.pipeline.settings.ai_provider = "rules"
    assert queue.run() is None
    assert queue.detail(created["id"])["ai_processing"]["attempts"] == 0
    queue.classifier.classify.assert_not_called()


def test_concurrent_reprocess_requests_enqueue_one_job(queue: QueueHarness) -> None:
    created = queue.create()
    queue.run()
    key = str(uuid4())
    barrier = Barrier(2)

    def reprocess():
        with queue.factory() as db:
            complaint = db.get(Complaint, created["id"])
            assert complaint is not None
            barrier.wait(timeout=5)
            result = queue.pipeline.reprocess(
                db, complaint, request_key=key, actor_id="review.demo"
            )
            assert result.ai_processing is not None
            return result.ai_processing.id

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="seongnam-queue-enqueue") as pool:
        futures = [pool.submit(reprocess) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1]
    detail = queue.detail(created["id"])
    assert sum(event["action"] == "ai_job_enqueued" for event in _queue_events(detail)) == 2
    assert detail["ai_processing"]["attempts"] == 0


def test_optional_drafting_metadata_cannot_leak_identifiers(queue: QueueHarness) -> None:
    provider = Mock(
        provider_name="synthetic",
        generate=Mock(
            return_value=StructuredDraftOutput(
                provider="010-1234-5678",
                sentences=[
                    GroundedDraftSentence(
                        text="합성 가로등 관리번호 확인이 필요합니다.",
                        substantive=True,
                        source_ids=["synthetic-source@example.com"],
                    )
                ],
            )
        ),
    )
    queue.pipeline.drafter.provider = provider
    created = queue.create()
    provider.generate.assert_not_called()  # Preflight always uses the local default drafter.
    assert queue.run().state == "completed"
    detail = queue.detail(created["id"])
    grounding = queue.client.get(f"/api/v1/complaints/{created['id']}/grounding")
    combined = json.dumps(detail) + grounding.text
    assert "010-1234-5678" not in combined
    assert "synthetic-source@example.com" not in combined
    assert grounding.json()["validation_status"] == "flagged"
    assert grounding.json()["provider"] == "deferred"
    assert detail["requires_human_review"] is True


def test_deferred_runtime_disables_hidden_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    classifier = Mock()
    monkeypatch.setattr("app.services.runtime.OpenAIClassifier", classifier)
    build_pipeline(
        Settings(
            ai_provider="openai",
            ai_deferred_enabled=True,
            openai_api_key=SecretStr("synthetic-key"),
        )
    )
    assert classifier.call_args.kwargs["max_retries"] == 0


def test_local_worker_command_processes_once_and_reports_only_metadata(
    queue: QueueHarness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    created = queue.create()
    monkeypatch.setattr("app.worker.Settings", Mock(return_value=queue.pipeline.settings))
    monkeypatch.setattr("app.worker.build_pipeline", Mock(return_value=queue.pipeline))
    monkeypatch.setattr(
        "app.worker.run_once",
        lambda factory, pipeline: run_once(factory, pipeline, clock=queue.clock),
    )
    assert main(["--once"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "job_id": created["ai_processing"]["id"],
        "state": "completed",
    }
    assert main(["--once"]) == 0
    assert json.loads(capsys.readouterr().out) == {"state": "idle"}


def test_keys_coalesced_while_active_stay_idempotent_after_completion(queue: QueueHarness) -> None:
    created = queue.create()
    path = f"/api/v1/complaints/{created['id']}/reprocess"
    key = str(uuid4())
    response = queue.client.post(path, headers={"Idempotency-Key": key})
    assert response.json()["ai_processing"]["id"] == created["ai_processing"]["id"]
    assert queue.run().state == "completed"
    assert (
        queue.client.post(
            f"/api/v1/complaints/{created['id']}/approve",
            json={"department_id": "ROAD_LIGHTING", "answer_draft": "합성 담당자 확인 답변입니다."},
        ).status_code
        == 200
    )
    before = queue.detail(created["id"])
    assert queue.client.post(path, headers={"Idempotency-Key": key}).status_code == 200
    assert queue.detail(created["id"]) == before
    assert queue.run() is None
    queue.classifier.classify.assert_called_once()


def test_sensitive_alternate_rule_candidate_also_bypasses_ai_queue(queue: QueueHarness) -> None:
    created = queue.create(content="합성 가로등 보안등 점등 불량입니다. 복지 상담도 문의합니다.")
    assert created["category"] == "streetlight"
    assert any(
        candidate["department_id"] == "WELFARE_REVIEW"
        for candidate in created["candidate_departments"]
    )
    assert created["ai_processing"] is None
    assert created["requires_human_review"] is True
    assert created["assigned_department_id"] is None
    assert queue.run() is None
    queue.classifier.classify.assert_not_called()


def test_queue_tables_are_additive_for_existing_sqlite_records(tmp_path: Path) -> None:
    settings = Settings(
        app_env="test", ai_provider="rules", database_url=f"sqlite:///{tmp_path / 'legacy.db'}"
    )
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(
        engine,
        tables=[
            table
            for table in Base.metadata.sorted_tables
            if table.name not in {"ai_processing_jobs", "ai_processing_requests"}
        ],
    )
    complaint_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(Complaint).values(
                id=complaint_id,
                title="합성 이전 민원",
                content="합성 기존 민원 내용입니다.",
                redacted_title="합성 이전 민원",
                redacted_content="합성 기존 민원 내용입니다.",
                status="needs_review",
            )
        )
    previous_columns = [column["name"] for column in inspect(engine).get_columns("complaints")]
    engine.dispose()
    app = create_app(settings)
    with TestClient(app) as client:
        _login_as(client, "auditor")
        response = client.get(f"/api/v1/complaints/{complaint_id}")
        assert response.status_code == 200
        assert response.json()["redacted_content"] == "합성 기존 민원 내용입니다."
        assert response.json()["ai_processing"] is None
        assert {"ai_processing_jobs", "ai_processing_requests"} <= set(
            inspect(app.state.engine).get_table_names()
        )
        assert [
            column["name"] for column in inspect(app.state.engine).get_columns("complaints")
        ] == previous_columns
