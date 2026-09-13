"""Club provider/queue contract tests use mocks or ASGITransport, never listeners or GPUs."""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.classifier_schemas import ClubClassifyRequest
from app.config import Settings
from app.model_gateway import GatewaySettings, create_gateway
from app.models import AIProcessingJob, AuditEvent, Complaint
from app.services.classifier import ClassifierError, DepartmentCatalog
from app.services.club_classifier import ClubClassifier
from app.services.runtime import build_pipeline
from app.worker import run_once
from tests.test_club_planner import Stream
from tests.test_model_gateway import KEY


def configuration(**overrides) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "ai_provider": "club",
        "ai_deferred_enabled": True,
        "classifier_endpoint_url": "https://models.example.test/v1/classify",
        "classifier_model_id": "synthetic-classifier-v1",
        "classifier_api_key": KEY,
    }
    values.update(overrides)
    return Settings(**values)


def classifier(handler=None, **overrides) -> ClubClassifier:
    settings = configuration(**overrides)
    return ClubClassifier(
        settings,
        DepartmentCatalog.from_json(settings.departments_path),
        transport=httpx.MockTransport(handler or reply_for),
    )


def reply_for(
    request: httpx.Request, mutate=None, *, status=200, headers=None, body=None
) -> httpx.Response:
    payload = json.loads(request.content)
    response = {
        "schema_version": "1",
        "request_id": payload["request_id"],
        "input_hash": payload["input_hash"],
        "model_id": payload["model_id"],
        "catalog_version": payload["catalog"]["version"],
        "catalog_sha256": payload["catalog"]["source_sha256"],
        "execution_mode": "model",
        "proposal": {
            "abstained": False,
            "category": "streetlight",
            "subcategory": "가로등·보안등 고장",
            "urgency": "normal",
            "candidates": [
                {"department_id": "ROAD_LIGHTING", "confidence": 0.99, "reason": "합성 분류 근거"}
            ],
            "missing_information": [],
            "evidence_summary": "합성 모델 응답: 조명 고장으로 제안합니다.",
        },
    }
    if mutate:
        mutate(response)
    return httpx.Response(
        status,
        headers=headers or {"content-type": "application/json"},
        stream=Stream(
            body if body is not None else json.dumps(response, ensure_ascii=False).encode()
        ),
    )


INPUT = {
    "title": "합성 가로등 고장",
    "text": "가상 공원 가로등이 꺼졌습니다.",
    "location_text": "가상 공원",
}


def test_explicit_provider_configuration_and_runtime_selection() -> None:
    assert Settings(_env_file=None, classifier_model_id="").ai_provider == "rules"
    with pytest.raises(ValidationError):
        configuration(ai_deferred_enabled=False)
    for field in ("classifier_endpoint_url", "classifier_api_key", "classifier_model_id"):
        with pytest.raises(ValidationError):
            configuration(**{field: ""})
    assert isinstance(build_pipeline(configuration()).classifier, ClubClassifier)
    assert build_pipeline(configuration()).deferred
    assert configuration().openai_api_key is None


@pytest.mark.parametrize(
    "url",
    [
        "http://models.example.test",
        "https://user:secret@models.example.test",
        "https://models.example.test?key=secret",
        "https://models.example.test#x",
        "file:///secret",
    ],
)
def test_remote_classifier_endpoint_boundary(url) -> None:
    with pytest.raises(ValidationError):
        configuration(classifier_endpoint_url=url)


def test_request_redaction_catalog_binding_and_mandatory_review() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return reply_for(request)

    model = classifier(handler)
    result = model.classify(
        **{**INPUT, "text": INPUT["text"] + " 연락처 010\n1111\n2222 synthetic@example.test"}
    )
    request = requests[0]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    for forbidden in (
        "synthetic@example.test",
        "citizen_session",
        "complaint_id",
        "keywords",
    ):
        assert forbidden not in request.content.decode()
    payload = ClubClassifyRequest.model_validate_json(request.content)
    assert "010" not in payload.complaint.content
    assert payload.catalog.version == model.catalog.catalog_version
    assert result.provider == "club" and result.requires_human_review
    assert result.candidates[0].department_id == "ROAD_LIGHTING"
    assert result.candidates[0].work_assignment_ids


@pytest.mark.parametrize(
    "problem",
    [
        "request",
        "hash",
        "model",
        "catalog_version",
        "catalog_hash",
        "mode",
        "unknown",
        "duplicate",
        "category",
        "subcategory",
        "pii",
        "escaped_pii",
        "extra",
        "nan",
        "abstain_with_candidate",
        "redirect",
        "status",
        "html",
        "compressed",
        "oversize",
        "malformed",
    ],
)
def test_classifier_rejects_untrusted_response_without_retry(problem) -> None:
    requests = []

    def mutate(data):
        proposal = data["proposal"]
        if problem in {"request", "hash", "model", "catalog_version", "catalog_hash", "mode"}:
            field, value = {
                "request": ("request_id", "11111111-1111-4111-8111-111111111111"),
                "hash": ("input_hash", "b" * 64),
                "model": ("model_id", "other-model"),
                "catalog_version": ("catalog_version", "other-catalog"),
                "catalog_hash": ("catalog_sha256", "b" * 64),
                "mode": ("execution_mode", "synthetic"),
            }[problem]
            data[field] = value
        elif problem == "unknown":
            proposal["candidates"][0]["department_id"] = "UNKNOWN"
        elif problem == "duplicate":
            proposal["candidates"].append(dict(proposal["candidates"][0]))
        elif problem in {"category", "subcategory"}:
            proposal[problem] = "invented"
        elif problem in {"pii", "escaped_pii"}:
            proposal["evidence_summary"] = "합성 연락처 " + (
                "010\n1111\n2222" if problem == "escaped_pii" else "010-1111-2222"
            )
        elif problem == "extra":
            proposal["execute_sql"] = "forbidden"
        elif problem == "nan":
            proposal["candidates"][0]["confidence"] = float("nan")
        elif problem == "abstain_with_candidate":
            proposal["abstained"] = True

    def handler(request):
        requests.append(request)
        options: dict[str, Any] = {}
        if problem in {"redirect", "status"}:
            options["status"] = 307 if problem == "redirect" else 500
        elif problem == "html":
            options["headers"] = {"content-type": "text/html"}
        elif problem == "compressed":
            options["headers"] = {"content-type": "application/json", "content-encoding": "gzip"}
        elif problem in {"oversize", "malformed"}:
            options["body"] = b"x" * 16_001 if problem == "oversize" else b"invalid secret reply"
        return reply_for(request, mutate, **options)

    with pytest.raises(ClassifierError, match="^club_classifier_failed$"):
        classifier(handler).classify(**INPUT)
    assert len(requests) == 1


def test_classifier_bounds_total_timeout_and_releases_capacity() -> None:
    stream = Stream(b"{}", delay=5)
    model = classifier(
        lambda request: httpx.Response(
            200, headers={"content-type": "application/json"}, stream=stream
        )
    )
    model.settings = model.settings.model_copy(update={"classifier_request_timeout_seconds": 0.02})
    with pytest.raises(ClassifierError):
        model.classify(**INPUT)
    assert stream.closed
    for _ in range(model.settings.classifier_max_concurrent):
        assert model.capacity.acquire(blocking=False)
    try:
        with pytest.raises(ClassifierError, match="busy"):
            model.classify(**INPUT)
    finally:
        for _ in range(model.settings.classifier_max_concurrent):
            model.capacity.release()


def test_synthetic_gateway_requires_explicit_classifier_response_mode() -> None:
    app = create_gateway(GatewaySettings(mode="synthetic", api_key=KEY))
    model = classifier()
    model.transport = httpx.ASGITransport(app=app)
    with pytest.raises(ClassifierError):
        model.classify(**INPUT)
    model.settings = configuration(classifier_response_mode="synthetic")
    result = model.classify(**INPUT)
    assert result.provider == "club_synthetic" and result.requires_human_review
    assert "합성 응답 시연" in result.evidence_summary
    assert result.candidates[0].department_id == model.catalog.fallback_department_id
    assert result.candidates[0].confidence == 0


def install_pipeline(test_app: FastAPI, *, synthetic=False):
    settings = configuration(
        database_url=str(test_app.state.engine.url),
        classifier_response_mode="synthetic" if synthetic else "model",
    )
    pipeline = build_pipeline(settings)
    assert isinstance(pipeline.classifier, ClubClassifier)
    pipeline.classifier.transport = (
        httpx.ASGITransport(app=create_gateway(GatewaySettings(mode="synthetic", api_key=KEY)))
        if synthetic
        else httpx.MockTransport(reply_for)
    )
    test_app.state.pipeline = pipeline
    return pipeline


@pytest.mark.parametrize("synthetic", [False, True])
def test_deferred_worker_integrates_club_without_openai_key_or_network(
    client: TestClient, test_app: FastAPI, synthetic
) -> None:
    pipeline = install_pipeline(test_app, synthetic=synthetic)
    created = client.post(
        "/api/v1/complaints",
        json={
            "title": INPUT["title"],
            "content": INPUT["text"],
            "location_text": INPUT["location_text"],
        },
    )
    assert created.status_code == 201
    identifier = created.json()["id"]
    with test_app.state.session_factory() as db:
        job = db.scalar(select(AIProcessingJob))
        assert job and job.state == "queued"
        assert job.model == pipeline.settings.classifier_model_id
        assert job.provider == pipeline.settings.classification_provider_label
    result = run_once(test_app.state.session_factory, pipeline)
    assert result and result.state == "completed"
    with test_app.state.session_factory() as db:
        complaint = db.get(Complaint, identifier)
        assert (
            complaint
            and complaint.status == "needs_review"
            and complaint.assigned_department_id is None
        )
        assert complaint.classifier_provider == ("club_synthetic" if synthetic else "club")
        audits = list(db.scalars(select(AuditEvent)))
        assert "ai_job_claimed" in {event.action for event in audits}
        assert "ai_job_completed" in {event.action for event in audits}
        assert "가상 공원 가로등" not in repr([event.details for event in audits])


@pytest.mark.parametrize(
    "change", ["model", "mode", "input_during_call", "human_review_during_call", "provider_error"]
)
def test_club_worker_rejects_changed_configuration_or_context(
    client: TestClient, test_app: FastAPI, change
) -> None:
    pipeline = install_pipeline(test_app)
    created = client.post(
        "/api/v1/complaints",
        json={
            "title": INPUT["title"],
            "content": INPUT["text"],
            "location_text": INPUT["location_text"],
        },
    ).json()
    if change == "model":
        pipeline.settings.classifier_model_id = "synthetic-new-version"
    elif change == "mode":
        pipeline.settings.classifier_response_mode = "synthetic"
    calls = []

    def handler(request):
        calls.append(request)
        if change == "provider_error":
            raise RuntimeError("synthetic sensitive failure 010-1111-2222")
        with test_app.state.session_factory() as db:
            complaint = db.get(Complaint, created["id"])
            assert complaint
            if change == "input_during_call":
                complaint.content += " 새 합성 문제 제보"
            elif change == "human_review_during_call":
                complaint.reviewed_at = datetime.now(UTC)
            db.commit()
        return reply_for(request)

    pipeline.classifier.transport = httpx.MockTransport(handler)
    result = run_once(test_app.state.session_factory, pipeline)
    assert result and result.state == ("queued" if change == "provider_error" else "failed")
    assert len(calls) == (0 if change in {"model", "mode"} else 1)
    with test_app.state.session_factory() as db:
        complaint = db.get(Complaint, created["id"])
        assert complaint and complaint.classifier_provider == "rules"
        assert "010-1111-2222" not in repr(
            [item.details for item in db.scalars(select(AuditEvent))]
        )
