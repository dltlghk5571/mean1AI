"""Verify only bounded, source-backed extraction suggestions cross the model boundary."""

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.extraction_schemas import ExtractionRequest, ExtractionResponse
from app.model_gateway import GatewaySettings, SyntheticBackend, create_gateway
from app.services.chat_extraction import DEMO_MODEL, DEMO_TEXT, ExtractionError, ExtractionRunner
from tests.test_chat_extraction import KEY, configuration, reply_for
from tests.test_club_planner import Stream
from tests.test_model_gateway import send


def test_exported_extraction_schemas_match_contract() -> None:
    root = Path(__file__).resolve().parents[1] / "docs/contracts"
    for suffix, schema in (("request", ExtractionRequest), ("response", ExtractionResponse)):
        assert (
            json.loads((root / f"intake-extraction-{suffix}.schema.json").read_text("utf-8"))
            == schema.model_json_schema()
        )


def test_club_requires_explicit_endpoint_and_shared_agent_credentials() -> None:
    for missing in ("chat_extraction_endpoint_url", "chat_model_id", "chat_api_key"):
        with pytest.raises(ValidationError):
            configuration("club", **{missing: ""})
    assert configuration("off", chat_api_key="", chat_model_id="").chat_extraction_provider == "off"
    assert configuration(
        "club", chat_extraction_endpoint_url="http://127.0.0.1:8001/v1/agent/extract"
    )
    for url in (
        "http://models.example.test",
        "https://user:key@models.example.test",
        "https://models.example.test?key=synthetic",
        "file:///secret",
    ):
        with pytest.raises(ValidationError):
            configuration("club", chat_extraction_endpoint_url=url)


def test_outbound_payload_masks_decoded_pii_and_excludes_private_state() -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return reply_for(request)

    runner = ExtractionRunner(configuration("club"), transport=httpx.MockTransport(handler))
    request = runner.make_request(DEMO_TEXT + " 합성 연락처 010\n1111\n2222 synthetic@example.test")
    assert "010" not in request.source_text and "synthetic@example.test" not in request.source_text
    result = runner.run(request)
    assert result.proposal.abstained
    sent = calls[0]
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert set(json.loads(sent.content)) == {
        "schema_version",
        "task",
        "request_id",
        "input_hash",
        "model_id",
        "source_text",
        "templates",
    }


@pytest.mark.parametrize(
    "fault",
    [
        "request",
        "model",
        "hash",
        "mode",
        "quote",
        "value",
        "topic",
        "answer",
        "duplicate",
        "safety_choice",
        "ambiguous",
        "multiple_issues",
        "extra",
        "escaped_pii",
        "length",
        "json",
        "html",
        "compressed",
        "oversize",
        "redirect",
        "synthetic_header",
    ],
)
def test_invalid_responses_fail_without_retry_or_body_echo(fault) -> None:
    calls = []

    def mutate(reply):
        proposal = reply["proposal"]
        if fault in {"request", "model", "hash", "mode"}:
            field, value = {
                "request": ("request_id", str(uuid4())),
                "model": ("model_id", "other-model"),
                "hash": ("input_hash", "a" * 64),
                "mode": ("execution_mode", "synthetic"),
            }[fault]
            reply[field] = value
        elif fault == "quote":
            proposal["purpose"]["quote"] = "원문에 없는 합성 요청"
        elif fault == "value":
            proposal["location"]["value"] = "지어낸 합성 주소"
        elif fault == "topic":
            proposal["topic"]["template_id"] = "unknown-topic"
        elif fault == "answer":
            proposal["answers"][0]["field_id"] = "unknown_field"
        elif fault == "duplicate":
            proposal["answers"].append(dict(proposal["answers"][0]))
        elif fault == "safety_choice":
            proposal["topic"]["template_id"] = "road"
            proposal["answers"][0]["field_id"] = "hazard_now"
        elif fault in {"ambiguous", "multiple_issues"}:
            proposal["reason"] = fault
        elif fault == "extra":
            proposal["submit_now"] = True
        elif fault == "escaped_pii":
            proposal["location"]["value"] = "010\n1111\n2222"
        elif fault == "length":
            proposal["location"]["value"] = "가" * 301

    def handler(request):
        calls.append(request)
        if fault == "redirect":
            return httpx.Response(307, headers={"location": "https://other.example.test"})
        response = reply_for(request, mutate)
        if fault in {"html", "compressed", "synthetic_header"}:
            field, value = {
                "html": ("content-type", "text/html"),
                "compressed": ("content-encoding", "gzip"),
                "synthetic_header": ("x-model-execution", "synthetic"),
            }[fault]
            response.headers[field] = value
        if fault in {"json", "oversize"}:
            response = httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=Stream(
                    b"x" * 12_001 if fault == "oversize" else b"invalid synthetic secret"
                ),
            )
        return response

    runner = ExtractionRunner(configuration("club"), transport=httpx.MockTransport(handler))
    with pytest.raises(ExtractionError, match="^extraction_failed$"):
        runner.run(runner.make_request(DEMO_TEXT))
    assert len(calls) == 1


def test_total_stream_timeout_releases_capacity() -> None:
    stream = Stream(b"{}", delay=5)
    runner = ExtractionRunner(
        configuration("club").model_copy(update={"chat_request_timeout_seconds": 0.02}),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "application/json"}, stream=stream
            )
        ),
    )
    with pytest.raises(ExtractionError):
        runner.run(runner.make_request(DEMO_TEXT))
    assert stream.closed
    for _ in range(4):
        assert runner.capacity.acquire(blocking=False)
    try:
        with pytest.raises(ExtractionError, match="busy"):
            runner.run(runner.make_request(DEMO_TEXT))
    finally:
        for _ in range(4):
            runner.capacity.release()


def test_gateway_extraction_echoes_identity_and_rejects_synthetic_in_club() -> None:
    settings = GatewaySettings(mode="synthetic", api_key=KEY, agent_model_id=DEMO_MODEL)
    app = create_gateway(settings)
    request = ExtractionRunner(configuration()).make_request(DEMO_TEXT)
    reply = send(
        app,
        "/v1/agent/extract",
        request.model_dump(mode="json"),
        headers={"Authorization": f"Bearer {KEY}"},
    )
    assert reply.status_code == 200
    parsed = ExtractionResponse.model_validate(reply.json())
    assert parsed.request_id == request.request_id and parsed.input_hash == request.input_hash
    assert parsed.execution_mode == "synthetic" and not parsed.proposal.abstained
    runner = ExtractionRunner(
        configuration("club", chat_model_id=DEMO_MODEL), transport=httpx.ASGITransport(app=app)
    )
    with pytest.raises(ExtractionError):
        runner.run(runner.make_request(DEMO_TEXT))


@pytest.mark.parametrize("fault,expected", [("hash", 422), ("pii", 422), ("backend", 502)])
def test_gateway_revalidates_extraction_input_and_backend_instances(fault, expected) -> None:
    class Double(SyntheticBackend):
        async def extract(self, request):
            proposal = await super().extract(request)
            if fault == "backend":
                assert proposal.location
                proposal.location.value = "지어낸 장소"
            request.request_id = uuid4()
            return proposal

    backend = Double()
    backend.execution_mode = "model"  # type: ignore[assignment]
    app = create_gateway(GatewaySettings(mode="backend", api_key=KEY), backend=backend)
    request = (
        ExtractionRunner(configuration("club")).make_request(DEMO_TEXT).model_dump(mode="json")
    )
    if fault == "hash":
        request["input_hash"] = "a" * 64
    elif fault == "pii":
        request["source_text"] += " 010\n1111\n2222"
    reply = send(app, "/v1/agent/extract", request, headers={"Authorization": f"Bearer {KEY}"})
    assert reply.status_code == expected and "지어낸" not in reply.text
