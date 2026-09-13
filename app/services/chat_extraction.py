"""Opt-in extraction transport and an explicitly fixed synthetic example; no keyword model."""

import asyncio
from threading import BoundedSemaphore
from uuid import uuid4

import httpx

from app.config import Settings
from app.extraction_schemas import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    AnswerSuggestion,
    ExtractionProposal,
    ExtractionRequest,
    ExtractionResponse,
    LocationSuggestion,
    PurposeSuggestion,
    TopicSuggestion,
    input_fingerprint,
    validate_proposal,
)
from app.services import citizen_questions as questions
from app.services.pii import contains_direct_identifiers, redact_pii

DEMO_TEXT = "합성 별빛공원 동문 앞 가로등이 어제 저녁부터 꺼져 있어요. 수리를 요청하고 싶어요."
DEMO_MODEL = "synthetic-extraction-v1"


def synthetic_proposal(request: ExtractionRequest) -> ExtractionProposal:
    if request.source_text == DEMO_TEXT:
        return ExtractionProposal(
            abstained=False,
            reason="supported",
            purpose=PurposeSuggestion(value="complaint", quote="수리를 요청하고 싶어요."),
            topic=TopicSuggestion(template_id="lighting", quote="가로등이"),
            location=LocationSuggestion(
                value="합성 별빛공원 동문 앞", quote="합성 별빛공원 동문 앞"
            ),
            answers=[
                AnswerSuggestion(
                    field_id="observed_time", value="어제 저녁부터", quote="어제 저녁부터"
                )
            ],
        )
    return ExtractionProposal(
        abstained=True,
        reason="insufficient_information",
        purpose=None,
        topic=None,
        location=None,
        answers=[],
    )


class ExtractionError(ValueError):
    pass


class ExtractionRunner:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.capacity = BoundedSemaphore(min(4, settings.chat_max_concurrent))

    @property
    def enabled(self) -> bool:
        return self.settings.chat_extraction_provider != "off"

    @property
    def model_id(self) -> str:
        return (
            DEMO_MODEL
            if self.settings.chat_extraction_provider == "demo"
            else (self.settings.chat_model_id or "")
        )

    def make_request(self, source_text: str) -> ExtractionRequest:
        source_text = redact_pii(source_text.strip()).text
        choices = [item.model_copy(deep=True) for item in questions.templates().values()]
        return ExtractionRequest(
            request_id=uuid4(),
            input_hash=input_fingerprint(source_text, choices),
            model_id=self.model_id,
            source_text=source_text,
            templates=choices,
        )

    def run(self, request: ExtractionRequest) -> ExtractionResponse:
        if not self.enabled:
            raise ExtractionError("extraction_disabled")
        if not self.capacity.acquire(blocking=False):
            raise ExtractionError("extraction_busy")
        try:
            if request.model_id != self.model_id:
                raise ValueError("extraction_model_changed")
            if self.settings.chat_extraction_provider == "demo":
                reply = ExtractionResponse(
                    request_id=request.request_id,
                    input_hash=request.input_hash,
                    model_id=request.model_id,
                    execution_mode="synthetic",
                    proposal=synthetic_proposal(request.model_copy(deep=True)),
                )
            else:
                reply = asyncio.run(self._request(request.model_copy(deep=True)))
            reply = ExtractionResponse.model_validate(reply.model_dump())
            if (
                reply.request_id != request.request_id
                or reply.input_hash != request.input_hash
                or reply.model_id != request.model_id
                or reply.execution_mode
                != ("synthetic" if self.settings.chat_extraction_provider == "demo" else "model")
            ):
                raise ValueError("extraction_response_mismatch")
            validate_proposal(request, reply.proposal)
            return reply
        except Exception:
            raise ExtractionError("extraction_failed") from None
        finally:
            self.capacity.release()

    async def _request(self, request: ExtractionRequest) -> ExtractionResponse:
        encoded = request.model_dump_json().encode()
        prose = [
            request.source_text,
            request.model_id,
            [item.model_dump() for item in request.templates],
        ]
        token = self.settings.chat_api_key
        if len(encoded) > MAX_REQUEST_BYTES or token is None or contains_direct_identifiers(prose):
            raise ValueError("extraction_request_rejected")
        timeout = min(15, self.settings.chat_request_timeout_seconds)
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(timeout),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self.settings.chat_extraction_endpoint_url or "",
                    content=encoded,
                    headers={
                        "Authorization": f"Bearer {token.get_secret_value()}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "X-Request-ID": str(request.request_id),
                    },
                ) as response:
                    if (
                        response.status_code != 200
                        or response.headers.get("content-type", "").split(";", 1)[0].strip()
                        != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"
                        or response.headers.get("x-model-execution", "model") != "model"
                        or int(response.headers.get("content-length", "0")) > MAX_RESPONSE_BYTES
                    ):
                        raise ValueError("extraction_response_rejected")
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ValueError("extraction_response_too_large")
        return ExtractionResponse.model_validate_json(body)
