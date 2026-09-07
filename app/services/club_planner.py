"""Opt-in club model transport. The model returns proposals, never database writes."""

import asyncio
import json
from uuid import uuid4

import httpx

from app.agent_schemas import ClubPlanRequest, ClubPlanResponse, PlanningContext
from app.config import Settings
from app.services.pii import redact_pii

MAX_REQUEST_BYTES = 200_000
MAX_RESPONSE_BYTES = 16_000


class ClubPlanner:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if settings.chat_provider != "club":
            raise ValueError("club_provider_must_be_explicit")
        self.settings = settings
        self.transport = transport

    def plan(self, context: PlanningContext) -> dict[str, object]:
        # Called inside the existing citizen request worker, not the ASGI event loop.
        try:
            return asyncio.run(self._request(context))
        except Exception:
            raise ValueError("club_model_request_failed") from None

    async def _request(self, context: PlanningContext) -> dict[str, object]:
        safe = PlanningContext.model_validate_json(redact_pii(context.model_dump_json()).text)
        safe.messages = safe.messages[-12:]
        payload = ClubPlanRequest(model_id=self.settings.chat_model_id or "", context=safe)
        encoded = payload.model_dump_json().encode()
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ValueError("club_context_too_large")
        timeout = min(self.settings.chat_request_timeout_seconds, safe.time_budget_seconds)
        token = self.settings.chat_api_key
        if token is None:
            raise ValueError("club_key_required")
        # asyncio.timeout bounds the entire exchange, including a slowly streamed body.
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(timeout),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self.settings.chat_endpoint_url or "",
                    content=encoded,
                    headers={
                        "Authorization": f"Bearer {token.get_secret_value()}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "X-Request-ID": str(uuid4()),
                    },
                ) as response:
                    if (
                        response.status_code != 200
                        or response.headers.get("content-type", "").split(";", 1)[0].strip()
                        != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"
                    ):
                        raise ValueError("club_response_rejected")
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                        raise ValueError("club_response_too_large")
                    data = bytearray()
                    async for chunk in response.aiter_raw():
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise ValueError("club_response_too_large")
        reply = ClubPlanResponse.model_validate(json.loads(data))
        if reply.model_id != payload.model_id:
            raise ValueError("club_model_mismatch")
        return reply.step.model_dump()
