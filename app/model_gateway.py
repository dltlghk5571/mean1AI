"""Model-team API scaffold. Importing/factory creation starts no listener, worker or model."""

import asyncio
import ctypes
import hmac
import sys
from threading import BoundedSemaphore
from typing import Literal, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.agent_schemas import STEP_ADAPTER, AgentStep, ClubPlanRequest, ClubPlanResponse
from app.classifier_schemas import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    ClassificationProposal,
    ClubClassifyRequest,
    ClubClassifyResponse,
    input_fingerprint,
)
from app.classifier_schemas import (
    validate_proposal as validate_classification,
)
from app.extraction_schemas import ExtractionProposal, ExtractionRequest, ExtractionResponse
from app.extraction_schemas import input_fingerprint as extraction_fingerprint
from app.extraction_schemas import validate_proposal as validate_extraction
from app.incident_compare_schemas import (
    ComparisonProposal,
    ComparisonRequest,
    ComparisonResponse,
    Relation,
)
from app.schemas import Urgency
from app.services.chat_extraction import synthetic_proposal
from app.services.citizen_agent import DemoToolPlanner
from app.services.incident_comparison import validate_proposal as validate_comparison
from app.services.pii import contains_direct_identifiers, redact_pii


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MODEL_GATEWAY_", env_file=None, extra="ignore", hide_input_in_errors=True
    )
    mode: Literal["disabled", "synthetic", "backend"] = "disabled"
    api_key: SecretStr | None = None
    agent_model_id: str = Field(default="synthetic-agent-v1", min_length=1, max_length=120)
    classifier_model_id: str = Field(
        default="synthetic-classifier-v1", min_length=1, max_length=120
    )
    request_timeout_seconds: float = Field(default=10, ge=1, le=30)
    max_concurrent: int = Field(default=2, ge=1, le=4)

    @model_validator(mode="after")
    def validate_configuration(self) -> "GatewaySettings":
        if self.mode != "disabled" and (
            self.api_key is None or not self.api_key.get_secret_value().strip()
        ):
            raise ValueError("model_gateway_key_required")
        for identifier in (self.agent_model_id, self.classifier_model_id):
            if not identifier.strip() or redact_pii(identifier).detected_types:
                raise ValueError("invalid_gateway_model_id")
            if self.mode == "synthetic" and not identifier.startswith("synthetic-"):
                raise ValueError("synthetic_gateway_requires_synthetic_model_ids")
        return self


class ModelBackend(Protocol):
    """Future inference adapters return proposals only. Gateway owns request identity/auth."""

    @property
    def execution_mode(self) -> Literal["model", "synthetic"]: ...

    async def plan(self, request: ClubPlanRequest) -> AgentStep: ...

    async def classify(self, request: ClubClassifyRequest) -> ClassificationProposal: ...

    async def compare(self, request: ComparisonRequest) -> ComparisonProposal: ...

    async def extract(self, request: ExtractionRequest) -> ExtractionProposal: ...


class SyntheticBackend:
    execution_mode: Literal["synthetic"] = "synthetic"

    async def plan(self, request: ClubPlanRequest) -> AgentStep:
        return STEP_ADAPTER.validate_python(DemoToolPlanner().plan(request.context))

    async def extract(self, request: ExtractionRequest) -> ExtractionProposal:
        return synthetic_proposal(request)

    async def classify(self, request: ClubClassifyRequest) -> ClassificationProposal:
        return ClassificationProposal(
            abstained=True,
            category=None,
            subcategory=None,
            urgency=Urgency.NORMAL,
            candidates=[],
            missing_information=[],
            evidence_summary=(
                "연결 흐름을 확인하는 합성 응답입니다. 실제 분류는 수행하지 않았습니다."
            ),
        )

    async def compare(self, request: ComparisonRequest) -> ComparisonProposal:
        return ComparisonProposal(
            relation=Relation.UNCERTAIN,
            summary="연결 흐름을 확인하는 합성 응답입니다. 실제 사건 비교는 수행하지 않았습니다.",
            evidence=[],
            differences=[],
            questions=["같은 시설과 발생 상황인지 확인해 주세요."],
        )


class GatewayError(ValueError):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.status = status


def _data(value: object) -> object:
    # Revalidate mutable model instances instead of trusting a backend's Python object.
    return value.model_dump() if isinstance(value, BaseModel) else value


def _prose(
    payload: ClubPlanRequest | ClubClassifyRequest | ComparisonRequest | ExtractionRequest,
) -> object:
    if isinstance(payload, ExtractionRequest):
        return [
            payload.source_text,
            payload.model_id,
            [item.model_dump() for item in payload.templates],
        ]
    if isinstance(payload, ClubPlanRequest):
        return [payload.context.model_dump(), payload.model_id]
    if isinstance(payload, ClubClassifyRequest):
        return (
            payload.complaint.model_dump(),
            payload.catalog.model_dump(exclude={"source_sha256"}),
            payload.model_id,
        )
    return [
        *(
            getattr(item, field)
            for item in (payload.current, payload.candidate)
            for field in ("title", "content", "location", "category")
        ),
        payload.model_id,
    ]


async def _read_payload(request: Request, limit: int) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise GatewayError("json_content_type_required", 415)
    if request.headers.get("content-encoding", "identity") != "identity":
        raise GatewayError("compressed_request_rejected", 415)
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise GatewayError("invalid_content_length", 400) from None
    if declared < 0 or declared > limit:
        raise GatewayError("request_too_large", 413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise GatewayError("request_too_large", 413)
    return bytes(body)


def create_gateway(
    settings: GatewaySettings | None = None, *, backend: ModelBackend | None = None
) -> FastAPI:
    settings = settings or GatewaySettings()
    if settings.mode == "synthetic":
        if backend is not None:
            raise ValueError("synthetic_mode_cannot_load_backend")
        backend = SyntheticBackend()
    elif settings.mode == "backend" and (backend is None or backend.execution_mode != "model"):
        raise ValueError("real_backend_not_implemented_or_not_injected")
    elif settings.mode == "disabled" and backend is not None:
        raise ValueError("disabled_gateway_cannot_load_backend")
    app = FastAPI(
        title="Seongnam model API scaffold", docs_url=None, redoc_url=None, openapi_url=None
    )
    capacity = BoundedSemaphore(settings.max_concurrent)
    app.state.capacity = capacity

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {"mode": settings.mode, "backend_configured": settings.mode == "backend"},
            headers={"Cache-Control": "no-store"},
        )

    async def dispatch(request: Request) -> JSONResponse:
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if settings.mode == "disabled":
            return JSONResponse({"error": "gateway_disabled"}, status_code=503, headers=headers)
        assert settings.api_key is not None and backend is not None
        expected = ("Bearer " + settings.api_key.get_secret_value()).encode()
        actual = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(actual, expected):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={**headers, "WWW-Authenticate": "Bearer"},
            )
        if not capacity.acquire(blocking=False):
            return JSONResponse({"error": "gateway_busy"}, status_code=429, headers=headers)
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                path = request.url.path
                limit = (
                    32_000
                    if path.endswith("/compare")
                    else 80_000
                    if path.endswith("/extract")
                    else MAX_REQUEST_BYTES
                )
                body = await _read_payload(request, limit)
                payload: (
                    ClubPlanRequest | ClubClassifyRequest | ComparisonRequest | ExtractionRequest
                )
                if path.endswith("/plan"):
                    payload = ClubPlanRequest.model_validate_json(body)
                elif path.endswith("/classify"):
                    payload = ClubClassifyRequest.model_validate_json(body)
                elif path.endswith("/extract"):
                    payload = ExtractionRequest.model_validate_json(body)
                else:
                    payload = ComparisonRequest.model_validate_json(body)
                model_id = (
                    settings.agent_model_id
                    if isinstance(payload, (ClubPlanRequest, ExtractionRequest))
                    else settings.classifier_model_id
                )
                if payload.model_id != model_id:
                    raise GatewayError("unknown_model_id", 404)
                if contains_direct_identifiers(_prose(payload)):
                    raise GatewayError("unredacted_input_rejected", 422)
                if isinstance(
                    payload, ClubClassifyRequest
                ) and payload.input_hash != input_fingerprint(payload.complaint, payload.catalog):
                    raise GatewayError("input_hash_mismatch", 422)
                if isinstance(payload, ComparisonRequest) and (
                    payload.current.ref,
                    payload.candidate.ref,
                ) != ("current", "candidate"):
                    raise GatewayError("invalid_comparison_refs", 422)
                if isinstance(
                    payload, ExtractionRequest
                ) and payload.input_hash != extraction_fingerprint(
                    payload.source_text, payload.templates
                ):
                    raise GatewayError("input_hash_mismatch", 422)
                # Providers get copies; they cannot change IDs/hash/catalog echoed by the gateway.
                try:
                    if isinstance(payload, ExtractionRequest):
                        extracted = ExtractionProposal.model_validate(
                            _data(await backend.extract(payload.model_copy(deep=True)))
                        )
                        validate_extraction(payload, extracted)
                        response = ExtractionResponse(
                            request_id=payload.request_id,
                            input_hash=payload.input_hash,
                            model_id=model_id,
                            execution_mode=backend.execution_mode,
                            proposal=extracted,
                        ).model_dump(mode="json")
                    elif isinstance(payload, ClubPlanRequest):
                        step = STEP_ADAPTER.validate_python(
                            _data(await backend.plan(payload.model_copy(deep=True)))
                        )
                        if contains_direct_identifiers(step.model_dump()):
                            raise ValueError("response_contains_identifier")
                        response = ClubPlanResponse(model_id=model_id, step=step).model_dump(
                            mode="json"
                        )
                    elif isinstance(payload, ClubClassifyRequest):
                        proposal = ClassificationProposal.model_validate(
                            _data(await backend.classify(payload.model_copy(deep=True)))
                        )
                        validate_classification(payload, proposal)
                        response = ClubClassifyResponse(
                            request_id=payload.request_id,
                            input_hash=payload.input_hash,
                            model_id=model_id,
                            catalog_version=payload.catalog.version,
                            catalog_sha256=payload.catalog.source_sha256,
                            execution_mode=backend.execution_mode,
                            proposal=proposal,
                        ).model_dump(mode="json")
                    else:
                        comparison = ComparisonResponse(
                            schema_version="1",
                            request_id=payload.request_id,
                            input_hash=payload.input_hash,
                            model_id=model_id,
                            proposal=ComparisonProposal.model_validate(
                                _data(await backend.compare(payload.model_copy(deep=True)))
                            ),
                        )
                        validate_comparison(payload, comparison)
                        if contains_direct_identifiers(comparison.proposal.model_dump()):
                            raise ValueError("response_contains_identifier")
                        response = comparison.model_dump(mode="json")
                except TimeoutError:
                    raise
                except Exception:
                    raise GatewayError("invalid_backend_result", 502) from None
                result = JSONResponse(
                    response, headers={**headers, "X-Model-Execution": backend.execution_mode}
                )
                maximum = (
                    12_000
                    if isinstance(payload, (ComparisonRequest, ExtractionRequest))
                    else MAX_RESPONSE_BYTES
                )
                if len(result.body) > maximum:
                    raise GatewayError("backend_result_too_large", 502)
                return result
        except GatewayError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=headers)
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=422, headers=headers)
        except TimeoutError:
            return JSONResponse({"error": "gateway_timeout"}, status_code=504, headers=headers)
        except Exception:
            return JSONResponse({"error": "gateway_failed"}, status_code=503, headers=headers)
        finally:
            capacity.release()

    for route in ("/v1/agent/plan", "/v1/agent/extract", "/v1/classify", "/v1/incident/compare"):
        app.add_api_route(route, dispatch, methods=["POST"], response_class=JSONResponse)
    return app


def serve_factory() -> FastAPI:
    """Explicit future uvicorn --factory target. Does not load a real inference backend."""
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleTitleW("Seongnam - model API gateway")
    return create_gateway()
