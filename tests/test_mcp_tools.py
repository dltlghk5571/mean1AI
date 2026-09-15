import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.mcp_check import check_connection
from app.mcp_server import build_server, open_database
from app.models import (
    CitizenChat,
    Complaint,
    MCPToolAuditEvent,
    ServiceCatalogReview,
    ServiceCatalogVersion,
)
from app.services.mcp_tools import CatalogToolGateway

PHONE = "010-1111-2222"  # Synthetic identifiers only.


@pytest.fixture(autouse=True)
def block_sdk_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args, **kwargs):
        raise AssertionError("MCP catalog tools must not make HTTP requests")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", blocked)


@pytest.fixture
def gateway(client: TestClient, test_app: FastAPI) -> CatalogToolGateway:
    return CatalogToolGateway(test_app.state.session_factory)


def publish(client: TestClient, bundle: dict) -> dict:
    response = client.post("/api/v1/service-catalogs", json=bundle)
    assert response.status_code == 201
    record = response.json()
    response = client.post(
        f"/api/v1/service-catalogs/{record['version']}/review",
        json={
            "content_hash": record["content_hash"],
            "decision": "approved",
            "review_due_at": (datetime.now(UTC).date() + timedelta(days=7)).isoformat(),
            "reason": "MCP 합성 검증 자료입니다.",
        },
    )
    assert response.status_code == 200
    return record


def test_only_published_current_services_are_returned(
    gateway: CatalogToolGateway, client: TestClient, service_bundle: dict, test_app: FastAPI
) -> None:
    assert gateway.call("search_services", {"query": "가로등"}).status == "catalog_unavailable"
    assert client.post("/api/v1/service-catalogs", json=service_bundle).status_code == 201
    assert not gateway.call("search_services", {"query": "가로등"}).services
    record = publish(client, service_bundle)
    result = gateway.call("search_services", {"query": f"가로등 {PHONE}", "limit": 1})
    assert [card.service_id for card in result.services] == ["DEMO-LIGHT"]
    assert result.catalog and result.catalog.content_hash == record["content_hash"]
    assert result.services[0].source_url is None and result.services[0].synthetic
    assert "합성" in result.message and PHONE not in result.model_dump_json()
    with test_app.state.session_factory() as db:
        events = list(db.scalars(select(MCPToolAuditEvent)))
        assert len(events) == 3 and events[-1].service_ids == ["DEMO-LIGHT"]
        assert events[-1].catalog_review_id == result.catalog.review_id
        assert PHONE not in str([vars(event) for event in events])
        assert "가로등" not in str([vars(event) for event in events])
        assert db.scalar(select(func.count(Complaint.id))) == 0
        assert db.scalar(select(func.count(CitizenChat.id))) == 0
        assert db.scalar(select(func.count(ServiceCatalogReview.id))) == 2


def test_requirements_bind_to_search_version_and_reapproval(
    gateway: CatalogToolGateway, client: TestClient, service_bundle: dict
) -> None:
    publish(client, service_bundle)
    result = gateway.call("search_services", {"query": "가로등"})
    assert result.catalog
    arguments: dict[str, object] = {
        "service_id": "DEMO-LIGHT",
        "catalog": result.catalog.model_dump(),
    }
    requirements = gateway.call("get_required_information", arguments)
    assert requirements.status == "ok" and requirements.services == result.services
    assert requirements.required_information
    # Reapproving identical bytes is a new review, so the old reference must not remain valid.
    publish(client, service_bundle)
    stale = gateway.call("get_required_information", arguments)
    assert stale.status == "stale_catalog" and not stale.services
    current = gateway.call("search_services", {"query": "가로등"})
    assert current.catalog
    arguments["catalog"] = current.catalog.model_dump()
    assert gateway.call("get_required_information", arguments).status == "ok"
    arguments["service_id"] = "DOES-NOT-EXIST"
    assert gateway.call("get_required_information", arguments).status == "not_found"


def test_withdrawal_and_service_dates_remove_previous_results(
    gateway: CatalogToolGateway, client: TestClient, service_bundle: dict
) -> None:
    record = publish(client, service_bundle)
    result = gateway.call("search_services", {"query": "가로등"})
    assert result.catalog
    assert (
        client.post(
            f"/api/v1/service-catalogs/{record['version']}/review",
            json={
                "content_hash": record["content_hash"],
                "decision": "withdrawn",
                "reason": "MCP 합성 자료를 철회합니다.",
            },
        ).status_code
        == 200
    )
    assert gateway.call("search_services", {"query": "가로등"}).status == "catalog_unavailable"
    response = gateway.call(
        "get_required_information",
        {"service_id": "DEMO-LIGHT", "catalog": result.catalog.model_dump()},
    )
    assert response.status == "catalog_unavailable" and not response.required_information
    service_bundle["version"] += "-expired"
    service_bundle["services"][0]["effective_until"] = "2000-01-01"
    publish(client, service_bundle)
    assert not gateway.call("search_services", {"query": "가로등"}).services


def test_common_questions_do_not_need_catalog_or_create_intake(gateway: CatalogToolGateway) -> None:
    result = gateway.call("get_required_information", {})
    assert result.status == "ok" and result.catalog is None and not result.services
    assert [item.field_id for item in result.required_information] == ["content", "location_text"]
    assert result.required_information[1].required is False


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_services", {}),
        ("search_services", {"query": "가" * 2001}),
        ("search_services", {"query": "가로등", "limit": 0}),
        ("search_services", {"query": "가로등", "limit": 4}),
        ("search_services", {"query": "가로등", "limit": True}),
        ("search_services", {"query": "가로등", "limit": "1"}),
        ("search_services", {"query": {"private": PHONE}}),
        ("search_services", {"query": "가로등", "private": PHONE}),
        ("get_required_information", {"service_id": "DEMO-LIGHT"}),
        ("get_required_information", {"service_id": None, "owner_session": PHONE}),
        ("submit_complaint", {"body": PHONE}),
        (PHONE, {"query": PHONE}),
    ],
)
def test_invalid_tool_inputs_are_not_echoed_or_audited_as_content(
    gateway: CatalogToolGateway, test_app: FastAPI, tool: str, arguments: dict
) -> None:
    async def run() -> None:
        with anyio.fail_after(10):
            async with Client(build_server(gateway), read_timeout_seconds=5) as client:
                response = await client.call_tool(tool, arguments)
                assert response.is_error
                assert response.structured_content
                assert response.structured_content["status"] == "invalid_input"
                assert PHONE not in response.model_dump_json()

    anyio.run(run)
    with test_app.state.session_factory() as db:
        event = db.scalar(select(MCPToolAuditEvent))
        assert event and event.status == "invalid_input"
        assert PHONE not in str(vars(event))
        assert not event.service_ids and event.catalog_version is None


def test_audit_failure_withholds_result(
    gateway: CatalogToolGateway,
    client: TestClient,
    service_bundle: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish(client, service_bundle)

    def failed_commit(*args, **kwargs):
        raise RuntimeError(PHONE)

    monkeypatch.setattr(Session, "commit", failed_commit)
    result = gateway.call("search_services", {"query": "가로등"})
    assert result.status == "error" and not result.services and result.catalog is None
    assert PHONE not in result.model_dump_json()


def test_mcp_journal_is_append_only(gateway: CatalogToolGateway, test_app: FastAPI) -> None:
    gateway.call("get_required_information", {})
    for statement in (
        "UPDATE mcp_tool_audit_events SET status='forged'",
        "DELETE FROM mcp_tool_audit_events",
    ):
        with test_app.state.session_factory() as db:
            with pytest.raises(IntegrityError, match="append-only"):
                db.execute(text(statement))
            db.rollback()
            assert db.scalar(select(func.count(MCPToolAuditEvent.id))) == 1


def test_corrupt_catalog_is_not_returned(
    gateway: CatalogToolGateway,
    client: TestClient,
    service_bundle: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish(client, service_bundle)
    original = Session.get

    def corrupt(self, entity, *args, **kwargs):
        record = original(self, entity, *args, **kwargs)
        if entity is ServiceCatalogVersion:
            assert record is not None
            record.bundle = json.loads(json.dumps(record.bundle))
            record.bundle["services"][0]["summary"] = PHONE
        return record

    monkeypatch.setattr(Session, "get", corrupt)
    result = gateway.call("search_services", {"query": "가로등"})
    assert result.status == "error" and not result.services
    assert PHONE not in result.model_dump_json()


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_stdio_discovery_calls_and_withdrawal(
    client: TestClient, test_app: FastAPI, service_bundle: dict, mode: str
) -> None:
    record = publish(client, service_bundle)
    path = test_app.state.engine.url.database
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", "-m", "app.mcp_server", "--database", path],
        cwd=Path(__file__).resolve().parents[1],
    )

    async def run() -> None:
        with anyio.fail_after(30):
            async with Client(
                parameters, mode=mode, read_timeout_seconds=10, cache=None
            ) as mcp_client:
                listing = await mcp_client.list_tools()
                assert {tool.name for tool in listing.tools} == {
                    "search_services",
                    "get_required_information",
                }
                for tool in listing.tools:
                    assert tool.input_schema["additionalProperties"] is False
                    assert tool.annotations and tool.annotations.read_only_hint
                    assert tool.annotations.open_world_hint is False
                response = await mcp_client.call_tool("search_services", {"query": "가로등"})
                data = response.structured_content
                assert not response.is_error and data
                assert data["services"][0]["service_id"] == "DEMO-LIGHT"
                assert isinstance(response.content[0], TextContent)
                assert json.loads(response.content[0].text) == data
                details = await mcp_client.call_tool(
                    "get_required_information",
                    {"service_id": "DEMO-LIGHT", "catalog": data["catalog"]},
                )
                assert not details.is_error and details.structured_content
                assert details.structured_content["required_information"]
                invalid = await mcp_client.call_tool(
                    "search_services", {"query": "가로등", "limit": PHONE}
                )
                assert invalid.is_error and PHONE not in invalid.model_dump_json()
                assert (
                    client.post(
                        f"/api/v1/service-catalogs/{record['version']}/review",
                        json={
                            "content_hash": record["content_hash"],
                            "decision": "withdrawn",
                            "reason": "연결 중 합성 자료 철회를 검증합니다.",
                        },
                    ).status_code
                    == 200
                )
                withdrawn = await mcp_client.call_tool("search_services", {"query": "가로등"})
                assert withdrawn.structured_content
                assert withdrawn.structured_content["status"] == "catalog_unavailable"
                assert not withdrawn.structured_content["services"]

    anyio.run(run)
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(MCPToolAuditEvent.id))) == 4
        assert db.scalar(select(func.count(Complaint.id))) == 0


def test_connection_check_does_not_publish_missing_catalog(
    gateway: CatalogToolGateway, test_app: FastAPI
) -> None:
    result = anyio.run(check_connection, Path(test_app.state.engine.url.database))
    assert result["connection"] == "ok" and result["catalog_status"] == "catalog_unavailable"
    assert result["question_count"] == 2 and result["service_count"] == 0
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(ServiceCatalogReview.id))) == 0


def test_missing_db_is_not_created_and_cli_error_has_no_private_path(tmp_path: Path) -> None:
    database = tmp_path / f"synthetic-{PHONE}.db"
    with pytest.raises(FileNotFoundError):
        open_database(database)
    process = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "app.mcp_server", "--database", str(database)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert process.returncode == 1 and process.stdout == ""
    assert "MCP 서버" in process.stderr and PHONE not in process.stderr
    assert not database.exists()


def test_database_requires_journal_guards(gateway: CatalogToolGateway, test_app: FastAPI) -> None:
    with test_app.state.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER prevent_mcp_tool_audit_events_update"))
    with pytest.raises(ValueError, match="prepare_database"):
        open_database(Path(test_app.state.engine.url.database))
