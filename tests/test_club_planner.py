import asyncio
import json
import re
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent_schemas import PlanningContext
from app.chat_schemas import AgentContext, ChatDraft, ChatMessage, ChatState
from app.config import Settings
from app.services.citizen_agent import AgentRunError, CitizenAgentExecutor, DemoToolPlanner
from app.services.club_planner import ClubPlanner


def configuration(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        chat_provider="club",
        chat_endpoint_url=overrides.pop("chat_endpoint_url", "https://models.example.test/plan"),
        chat_model_id="synthetic-agent",
        chat_api_key="synthetic-test-key",
        **overrides,
    )


def context() -> PlanningContext:
    return PlanningContext(
        stage="information",
        draft=ChatDraft(title="합성 질문", content="가로등 정보를 알려 주세요."),
        messages=[ChatMessage(role="user", text="합성 연락처 010-1111-2222")],
        remaining_tool_calls=3,
    )


class Stream(httpx.AsyncByteStream):
    def __init__(self, data: bytes, *, delay: float = 0) -> None:
        self.data = data
        self.delay = delay
        self.closed = False

    async def __aiter__(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        yield self.data

    async def aclose(self) -> None:
        self.closed = True


def response(step: dict, **overrides) -> httpx.Response:
    body = json.dumps({"schema_version": "1", "model_id": "synthetic-agent", "step": step}).encode()
    return httpx.Response(
        overrides.pop("status", 200),
        headers=overrides.pop("headers", {"content-type": "application/json"}),
        stream=Stream(overrides.pop("body", body)),
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://models.example.test/plan",
        "https://user:secret@models.example.test/plan",
        "https://models.example.test/plan?key=secret",
        "file:///secret",
        "https://models.example.test/plan#fragment",
        "https://models.example.test:invalid/plan",
    ],
)
def test_remote_endpoint_requires_secure_explicit_configuration(url: str) -> None:
    with pytest.raises(ValidationError):
        configuration(chat_endpoint_url=url)


def test_empty_config_keeps_offline_default_and_club_requires_credentials() -> None:
    assert Settings(_env_file=None, chat_endpoint_url="").chat_endpoint_url is None
    with pytest.raises(ValidationError):
        Settings(_env_file=None, chat_provider="club")
    assert configuration(chat_endpoint_url="http://127.0.0.1:8001/plan")


def test_transport_redacts_context_authenticates_and_returns_only_step() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response({"kind": "answer", "service_ids": []})

    planner = ClubPlanner(configuration(), transport=httpx.MockTransport(handler))
    original = context()
    result = planner.plan(original)
    assert result == {"kind": "answer", "service_ids": []}
    assert len(requests) == 1
    sent = requests[0]
    assert sent.method == "POST" and str(sent.url) == "https://models.example.test/plan"
    assert sent.headers["authorization"] == "Bearer synthetic-test-key"
    assert "010-1111-2222" not in sent.content.decode()
    assert "[전화번호]" in sent.content.decode()
    assert "010-1111-2222" in original.messages[0].text


@pytest.mark.parametrize(
    "problem",
    ["redirect", "status", "html", "compressed", "oversize", "invalid", "model", "tool"],
)
def test_transport_rejects_untrusted_responses_without_retry(problem: str) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        changes: dict[str, dict[str, object]] = {
            "redirect": {"status": 307, "headers": {"location": "https://other.test/plan"}},
            "status": {"status": 503},
            "html": {"headers": {"content-type": "text/html"}},
            "compressed": {
                "headers": {"content-type": "application/json", "content-encoding": "gzip"}
            },
            "oversize": {"body": b"x" * 16_001},
            "invalid": {"body": b"not-json synthetic-secret"},
            "model": {
                "body": b'{"schema_version":"1","model_id":"other","step":{"kind":"review"}}'
            },
            "tool": {
                "body": b'{"schema_version":"1","model_id":"synthetic-agent",'
                b'"step":{"kind":"submit"}}'
            },
        }
        return response({"kind": "answer", "service_ids": []}, **changes[problem])

    planner = ClubPlanner(configuration(), transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="^club_model_request_failed$"):
        planner.plan(context())
    assert count == 1


def test_total_timeout_cancels_stream_and_closes_connection() -> None:
    stream = Stream(b"{}", delay=5)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "application/json"}, stream=stream
        )
    )
    planner = ClubPlanner(configuration(), transport=transport)
    planning = context().model_copy(update={"time_budget_seconds": 0.02})
    with pytest.raises(ValueError, match="club_model_request_failed"):
        planner.plan(planning)
    assert stream.closed


def test_executor_capacity_is_released_after_failure(
    test_app: FastAPI, anonymous_client: TestClient
) -> None:
    executor = CitizenAgentExecutor(DemoToolPlanner(), concurrency=1)
    current = AgentContext(
        state=ChatState(stage="information"), action="information", expected_stage="information"
    )
    with test_app.state.session_factory() as db:
        assert executor.capacity.acquire(blocking=False)
        with pytest.raises(AgentRunError) as failure:
            executor.execute(db, current)
        assert failure.value.events == [{"status": "busy"}]
        executor.capacity.release()
        executor.timeout = -1
        with pytest.raises(AgentRunError):
            executor.execute(db, current)
        executor.timeout = 30
        assert executor.execute(db, current).reply.next_stage == "information"


def test_club_transport_drives_existing_tool_loop(
    test_app: FastAPI, anonymous_client: TestClient
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        planning = PlanningContext.model_validate(json.loads(request.content)["context"])
        step = DemoToolPlanner().plan(planning)
        calls.append(str(step["kind"]))
        return response(step)

    test_app.state.agent_executor = CitizenAgentExecutor(
        ClubPlanner(configuration(), transport=httpx.MockTransport(handler))
    )
    page = anonymous_client.get("/minwon/new")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf
    anonymous_client.headers["X-Citizen-CSRF"] = csrf[1]
    state = anonymous_client.post("/minwon/chat/open", json={}).json()
    result = anonymous_client.post(
        "/minwon/chat/turn",
        json={
            "revision": str(state["revision"]),
            "request_id": str(uuid4()),
            "action": "information",
        },
    )
    assert result.status_code == 200
    state = result.json()
    assert state["stage"] == "information" and calls == ["tool", "answer"]
