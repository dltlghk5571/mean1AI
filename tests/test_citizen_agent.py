import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent_schemas import PlanningContext
from app.chat_schemas import AgentContext, ChatDraft, ChatMessage, ChatState
from app.models import CitizenChat, CitizenChatAuditEvent, Complaint
from app.services.citizen_agent import AgentRunError, CitizenAgentExecutor, DemoToolPlanner


def publish(client: TestClient, bundle: dict) -> dict:
    response = client.post("/api/v1/service-catalogs", json=bundle)
    assert response.status_code == 201, response.text
    record = response.json()
    response = client.post(
        f"/api/v1/service-catalogs/{record['version']}/review",
        json={
            "content_hash": record["content_hash"],
            "decision": "approved",
            "review_due_at": (datetime.now(UTC).date() + timedelta(days=7)).isoformat(),
            "reason": "합성 에이전트 검증 자료를 확인했습니다.",
        },
    )
    assert response.status_code == 200, response.text
    return record


def chat_start(client: TestClient) -> dict:
    page = client.get("/minwon/new")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token
    client.headers["X-Citizen-CSRF"] = token[1]
    return client.post("/minwon/chat/open", json={}).json()


def chat_turn(client: TestClient, state: dict, action: str, **values: str) -> dict:
    response = client.post(
        "/minwon/chat/turn",
        json={
            "revision": str(state["revision"]),
            "request_id": str(uuid4()),
            "action": action,
            **values,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def context(query: str = "가로등", *, stage="information") -> AgentContext:
    return AgentContext(
        state=ChatState(
            stage=stage,
            draft=ChatDraft(title="합성 질문", content=query),
            messages=[ChatMessage(role="user", text=query)],
        ),
        action="information",
        expected_stage=stage,
    )


def test_agent_reads_only_published_catalog_and_excludes_expired_services(
    client: TestClient, test_app: FastAPI, service_bundle: dict
) -> None:
    executor = CitizenAgentExecutor(DemoToolPlanner())
    with test_app.state.session_factory() as db:
        result = executor.execute(db, context())
        assert not result.cards and "찾지 못했어요" in result.reply.message
    response = client.post("/api/v1/service-catalogs", json=service_bundle)
    assert response.status_code == 201
    with test_app.state.session_factory() as db:
        assert not executor.execute(db, context()).cards
    publish(client, service_bundle)
    with test_app.state.session_factory() as db:
        result = executor.execute(db, context())
        assert [card.service_id for card in result.cards] == ["DEMO-LIGHT"]
        assert "합성 자료" in result.reply.message
        assert result.cards[0].source_url is None
    service_bundle["version"] += "-expired"
    service_bundle["services"][0]["effective_until"] = "2000-01-01"
    publish(client, service_bundle)
    with test_app.state.session_factory() as db:
        assert not executor.execute(db, context()).cards


def test_chat_persists_tool_audit_without_queries_and_hides_withdrawn_cards(
    client: TestClient, test_app: FastAPI, service_bundle: dict
) -> None:
    record = publish(client, service_bundle)
    test_app.state.agent_executor = CitizenAgentExecutor(DemoToolPlanner())
    state = chat_turn(client, chat_start(client), "say", message="가로등 합성 연락처 010-1111-2222")
    state = chat_turn(client, state, "information")
    assert state["service_cards"][0]["service_id"] == "DEMO-LIGHT"
    assert "010-1111-2222" not in str(state)
    with test_app.state.session_factory() as db:
        events = db.scalars(
            select(CitizenChatAuditEvent).where(
                CitizenChatAuditEvent.action == "agent_step_completed"
            )
        ).all()
        assert any(event.details.get("tool") == "search_services" for event in events)
        assert "010-1111-2222" not in str([event.details for event in events])
        assert "합성 연락처" not in str([event.details for event in events])
        assert db.scalar(select(func.count(Complaint.id))) == 0
    response = client.post(
        f"/api/v1/service-catalogs/{record['version']}/review",
        json={
            "content_hash": record["content_hash"],
            "decision": "withdrawn",
            "reason": "시연 자료를 철회합니다.",
        },
    )
    assert response.status_code == 200
    assert client.post("/minwon/chat/open", json={}).json()["service_cards"] == []


def test_tool_agent_questions_review_and_explicit_submission_work(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    test_app.state.agent_executor = CitizenAgentExecutor(DemoToolPlanner())
    state = chat_turn(anonymous_client, chat_start(anonymous_client), "complaint")
    assert state["stage"] == "description"
    state = chat_turn(anonymous_client, state, "say", message="가상 공원 시설이 부서졌어요.")
    assert state["stage"] == "location"
    state = chat_turn(anonymous_client, state, "skip_location")
    assert state["stage"] == "review"
    result = chat_turn(anonymous_client, state, "confirm", consent="yes")
    assert anonymous_client.get(result["redirect"]).status_code == 200


class SequencePlanner:
    def __init__(self, steps: list[dict[str, object]]) -> None:
        self.steps = iter(steps)
        self.seen: list[PlanningContext] = []

    def plan(self, context: PlanningContext) -> dict[str, object]:
        self.seen.append(context.model_copy(deep=True))
        # Provider mutations must not change later context, tool results, or server drafts.
        context.draft.content = "attempted replacement"
        context.observations.clear()
        return next(self.steps)


def test_multi_tool_loop_uses_ids_and_questions_from_retrieved_data(
    client: TestClient, test_app: FastAPI, service_bundle: dict
) -> None:
    publish(client, service_bundle)
    planner = SequencePlanner(
        [
            {
                "kind": "tool",
                "call": {"name": "search_services", "call_id": "search", "query": "가로등"},
            },
            {
                "kind": "tool",
                "call": {
                    "name": "get_required_information",
                    "call_id": "fields",
                    "service_id": "DEMO-LIGHT",
                },
            },
            {"kind": "ask", "field_id": "location_text"},
        ]
    )
    with test_app.state.session_factory() as db:
        result = CitizenAgentExecutor(planner).execute(
            db, context("가로등 합성 010-1111-2222", stage="location")
        )
    assert (
        result.reply.message == service_bundle["services"][0]["required_information"][0]["question"]
    )
    assert len(result.events) == 3 and len(planner.seen[-1].observations) == 2
    assert all("010-1111-2222" not in item.model_dump_json() for item in planner.seen)
    assert all("attempted replacement" not in item.draft.content for item in planner.seen)


@pytest.mark.parametrize(
    "step",
    [
        {"kind": "submit", "consent": True},
        {
            "kind": "tool",
            "call": {"name": "fetch_url", "url": "http://127.0.0.1/private", "call_id": "fetch"},
        },
        {
            "kind": "tool",
            "call": {
                "name": "search_services",
                "call_id": "search",
                "query": "가로등",
                "owner_id": "other",
            },
        },
        {
            "kind": "tool",
            "call": {
                "name": "search_services",
                "call_id": "search",
                "query": "가로등",
                "limit": 100,
            },
        },
        {
            "kind": "tool",
            "call": {
                "name": "get_required_information",
                "call_id": "fields",
                "service_id": "not_retrieved",
            },
        },
        {
            "kind": "tool",
            "call": {"name": "search_services", "call_id": "010-1111-2222", "query": "가로등"},
        },
        {"kind": "answer", "service_ids": ["invented"]},
        {"kind": "review"},
    ],
)
def test_untrusted_plans_cannot_expand_tools_sources_or_state(
    step: dict, test_app: FastAPI, anonymous_client: TestClient
) -> None:
    with test_app.state.session_factory() as db:
        with pytest.raises(AgentRunError):
            CitizenAgentExecutor(SequencePlanner([step])).execute(db, context())
        assert db.scalar(select(func.count(Complaint.id))) == 0


@pytest.mark.parametrize("duplicate", [True, False])
def test_tool_repetition_and_budget_stop(
    duplicate: bool, test_app: FastAPI, anonymous_client: TestClient
) -> None:
    steps: list[dict[str, object]] = [
        {
            "kind": "tool",
            "call": {
                "name": "search_services",
                "call_id": "same" if duplicate else f"call_{index}",
                "query": "가로등",
            },
        }
        for index in range(4)
    ]
    planner = SequencePlanner(steps)
    with test_app.state.session_factory() as db, pytest.raises(AgentRunError) as caught:
        CitizenAgentExecutor(planner).execute(db, context())
    assert len(caught.value.events) == (1 if duplicate else 3)
    assert len(planner.seen) == (2 if duplicate else 4)


def test_failed_plan_rolls_back_chat_and_audits_attempts(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    planner = SequencePlanner(
        [
            {
                "kind": "tool",
                "call": {"name": "search_services", "call_id": "search", "query": "가로등"},
            },
            {"kind": "answer", "service_ids": ["fabricated"]},
        ]
    )
    test_app.state.agent_executor = CitizenAgentExecutor(planner)
    state = chat_start(anonymous_client)
    response = anonymous_client.post(
        "/minwon/chat/turn",
        json={
            "revision": str(state["revision"]),
            "request_id": str(uuid4()),
            "action": "information",
        },
    )
    assert response.status_code == 503
    assert anonymous_client.post("/minwon/chat/open", json={}).json() == state
    with test_app.state.session_factory() as db:
        assert (
            db.scalar(
                select(func.count(CitizenChatAuditEvent.id)).where(
                    CitizenChatAuditEvent.action == "agent_step_attempted"
                )
            )
            == 1
        )


def test_catalog_change_during_turn_prevents_stale_answer_commit(
    client: TestClient, test_app: FastAPI, service_bundle: dict
) -> None:
    record = publish(client, service_bundle)

    class WithdrawingPlanner(DemoToolPlanner):
        def plan(self, planning: PlanningContext) -> dict[str, object]:
            if planning.observations:
                response = client.post(
                    f"/api/v1/service-catalogs/{record['version']}/review",
                    json={
                        "content_hash": record["content_hash"],
                        "decision": "withdrawn",
                        "reason": "검수 데이터 변경을 시험합니다.",
                    },
                )
                assert response.status_code == 200
            return super().plan(planning)

    test_app.state.agent_executor = CitizenAgentExecutor(WithdrawingPlanner())
    state = chat_start(client)
    response = client.post(
        "/minwon/chat/turn",
        json={
            "revision": str(state["revision"]),
            "request_id": str(uuid4()),
            "action": "information",
        },
    )
    assert response.status_code == 503
    with test_app.state.session_factory() as db:
        chat = db.scalar(select(CitizenChat))
        assert chat and chat.revision == state["revision"] and not chat.state["service_cards"]
        assert (
            db.scalar(
                select(func.count(CitizenChatAuditEvent.id)).where(
                    CitizenChatAuditEvent.action == "agent_step_aborted"
                )
            )
            == 2
        )
