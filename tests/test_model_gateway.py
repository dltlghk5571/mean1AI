"""In-process ASGI only: no listening socket, model process or external HTTP."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.agent_schemas import ClubPlanRequest, ClubPlanResponse, PlanningContext
from app.chat_schemas import ChatDraft
from app.classifier_schemas import (
    ClassifierCatalog,
    ClassifierDepartment,
    ClassifierInput,
    ClubClassifyRequest,
    ClubClassifyResponse,
    input_fingerprint,
)
from app.incident_compare_schemas import ComparisonInput, ComparisonRequest, ComparisonResponse
from app.model_gateway import GatewaySettings, SyntheticBackend, create_gateway

KEY = "synthetic-gateway-test-key"
AUTH = {"Authorization": f"Bearer {KEY}"}


@pytest.mark.parametrize(
    "name,model",
    [
        ("club-classification-request", ClubClassifyRequest),
        ("club-classification-response", ClubClassifyResponse),
        ("club-plan-request", ClubPlanRequest),
        ("club-plan-response", ClubPlanResponse),
    ],
)
def test_published_schemas_match_model_team_contract(name, model) -> None:
    path = Path(__file__).resolve().parents[1] / "docs/contracts" / f"{name}.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == model.model_json_schema()


def gateway_config(**overrides) -> GatewaySettings:
    return GatewaySettings(mode="synthetic", api_key=KEY, **overrides)


def classification_request() -> ClubClassifyRequest:
    complaint = ClassifierInput(
        title="합성 조명 제보", content="가상 공원 조명이 꺼졌습니다.", location_text="가상 공원"
    )
    catalog = ClassifierCatalog(
        version="synthetic-v1",
        source_sha256="a" * 64,
        synthetic=True,
        fallback_department_id="DEMO_OTHER",
        departments=[
            ClassifierDepartment(
                id="DEMO_OTHER",
                name="합성 검토팀",
                category="other",
                description="합성 부서",
                jurisdiction="합성 지역",
                subcategories=[],
            )
        ],
    )
    return ClubClassifyRequest(
        request_id=uuid4(),
        input_hash=input_fingerprint(complaint, catalog),
        model_id="synthetic-classifier-v1",
        complaint=complaint,
        catalog=catalog,
    )


def plan_request() -> ClubPlanRequest:
    return ClubPlanRequest(
        model_id="synthetic-agent-v1",
        context=PlanningContext(
            stage="information",
            draft=ChatDraft(content="가로등"),
            messages=[],
            remaining_tool_calls=3,
        ),
    )


def comparison_request() -> ComparisonRequest:
    data = dict(
        title="합성 조명 문제",
        content="가상 시설의 조명이 꺼졌습니다.",
        location="가상 공원",
        content_truncated=False,
        category="streetlight",
        submitted_at=datetime.now(UTC),
        urgency="normal",
        field_status="none",
    )
    return ComparisonRequest(
        request_id=uuid4(),
        input_hash="a" * 64,
        model_id="synthetic-classifier-v1",
        current=ComparisonInput(ref="current", **data),
        candidate=ComparisonInput(ref="candidate", **data),
    )


def send(app: FastAPI, path: str, payload, *, headers=None, **kwargs) -> httpx.Response:
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
        ) as client:
            return await client.post(
                path, json=payload, headers=AUTH if headers is None else headers, **kwargs
            )

    return asyncio.run(run())


def test_factory_is_disabled_and_cannot_silently_start_real_backend(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_gateway(GatewaySettings())
    assert send(app, "/v1/classify", {}).status_code == 503
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValidationError):
        GatewaySettings(mode="synthetic")
    with pytest.raises(ValidationError):
        gateway_config(agent_model_id="real-sounding-model")
    with pytest.raises(ValueError, match="real_backend_not_implemented"):
        create_gateway(GatewaySettings(mode="backend", api_key=KEY))
    with pytest.raises(ValueError):
        create_gateway(gateway_config(), backend=SyntheticBackend())


@pytest.mark.parametrize(
    "path,factory,response_type",
    [
        ("/v1/agent/plan", plan_request, ClubPlanResponse),
        ("/v1/classify", classification_request, ClubClassifyResponse),
        ("/v1/incident/compare", comparison_request, ComparisonResponse),
    ],
)
def test_three_synthetic_endpoints_preserve_contract_identity(path, factory, response_type) -> None:
    payload = factory()
    response = send(create_gateway(gateway_config()), path, payload.model_dump(mode="json"))
    assert response.status_code == 200
    assert response.headers["x-model-execution"] == "synthetic"
    assert response.headers["cache-control"] == "no-store"
    parsed = response_type.model_validate(response.json())
    assert parsed.model_id == payload.model_id
    if isinstance(parsed, ClubClassifyResponse):
        assert parsed.proposal.abstained and not parsed.proposal.candidates
        assert parsed.execution_mode == "synthetic"
    if not isinstance(parsed, ClubPlanResponse):
        assert parsed.request_id == payload.request_id and parsed.input_hash == payload.input_hash


@pytest.mark.parametrize(
    "problem,expected",
    [
        ("auth", 401),
        ("extra", 422),
        ("model", 404),
        ("hash", 422),
        ("pii", 422),
        ("escaped_pii", 422),
        ("content_type", 415),
        ("compressed", 415),
        ("large", 413),
    ],
)
def test_gateway_rejects_invalid_inputs_without_echoing_body(problem, expected) -> None:
    payload = classification_request().model_dump(mode="json")
    headers = dict(AUTH)
    if problem == "auth":
        headers = {}
    elif problem == "extra":
        payload["database_query"] = "synthetic forbidden instruction"
    elif problem == "model":
        payload["model_id"] = "unknown"
    elif problem == "hash":
        payload["input_hash"] = "b" * 64
    elif problem in {"pii", "escaped_pii"}:
        payload["complaint"]["content"] += (
            " 010\n1111\n2222" if problem == "escaped_pii" else " 010-1111-2222"
        )
    elif problem == "content_type":
        headers["content-type"] = "text/plain"
    elif problem == "compressed":
        headers["content-encoding"] = "gzip"
    elif problem == "large":
        payload["complaint"]["content"] = "x" * 200_001
    response = send(create_gateway(gateway_config()), "/v1/classify", payload, headers=headers)
    assert response.status_code == expected
    assert "010" not in response.text and "forbidden instruction" not in response.text


def test_authentication_precedes_streaming_body_and_chunk_limit_is_enforced() -> None:
    async def run() -> None:
        app = create_gateway(gateway_config())

        async def forbidden_body():
            raise AssertionError("Unauthenticated request body was read")
            yield b""

        async def oversized():
            yield b" " * 100_000
            yield b" " * 100_001

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
        ) as client:
            assert (await client.post("/v1/classify", content=forbidden_body())).status_code == 401
            response = await client.post(
                "/v1/classify",
                content=oversized(),
                headers={**AUTH, "Content-Type": "application/json"},
            )
            assert response.status_code == 413

    asyncio.run(run())


@pytest.mark.parametrize("fault,status", [("timeout", 504), ("pii", 502), ("invalid", 502)])
def test_backend_failure_and_capacity_release(fault, status) -> None:
    class Double(SyntheticBackend):
        async def classify(self, request):
            if fault == "timeout":
                await asyncio.sleep(5)
            proposal = await super().classify(request)
            if fault == "pii":
                proposal.evidence_summary = "합성 연락처 010\n1111\n2222"
                return proposal
            return {"execute_sql": "synthetic private instruction"}

    # Test-only injection exercises the real-backend boundary without loading an inference engine.
    backend = Double()
    backend.execution_mode = "model"  # type: ignore[assignment]
    settings = GatewaySettings(mode="backend", api_key=KEY).model_copy(
        update={"request_timeout_seconds": 0.02}
    )
    app = create_gateway(settings, backend=backend)
    response = send(app, "/v1/classify", classification_request().model_dump(mode="json"))
    assert response.status_code == status and "010" not in response.text
    assert app.state.capacity.acquire(blocking=False)
    app.state.capacity.release()
    for _ in range(settings.max_concurrent):
        assert app.state.capacity.acquire(blocking=False)
    try:
        assert send(app, "/v1/classify", {}).status_code == 429
    finally:
        for _ in range(settings.max_concurrent):
            app.state.capacity.release()


def test_backend_cannot_modify_echoed_identity() -> None:
    class Double(SyntheticBackend):
        async def classify(self, request):
            request.request_id = uuid4()
            request.input_hash = "b" * 64
            request.catalog.version = "tampered"
            return await super().classify(request)

    backend = Double()
    backend.execution_mode = "model"  # type: ignore[assignment]
    app = create_gateway(GatewaySettings(mode="backend", api_key=KEY), backend=backend)
    payload = classification_request()
    response = send(app, "/v1/classify", payload.model_dump(mode="json"))
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == str(payload.request_id)
    assert data["input_hash"] == payload.input_hash
    assert data["catalog_version"] == payload.catalog.version


def test_slow_upload_is_cancelled_within_total_budget() -> None:
    async def run() -> None:
        settings = gateway_config().model_copy(update={"request_timeout_seconds": 0.02})
        app = create_gateway(settings)
        cancelled = False

        async def body():
            nonlocal cancelled
            try:
                yield b"{"
                await asyncio.sleep(5)
                yield b"}"
            finally:
                cancelled = True

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
        ) as client:
            response = await client.post(
                "/v1/classify", content=body(), headers={**AUTH, "Content-Type": "application/json"}
            )
        assert response.status_code == 504 and cancelled
        assert app.state.capacity.acquire(blocking=False)
        app.state.capacity.release()

    asyncio.run(run())
