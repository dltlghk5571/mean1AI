import json
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def service_bundle() -> dict:
    path = Path(__file__).resolve().parents[1] / "app/data/service_catalog_demo.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """All tests use in-process ASGI or explicit provider mocks; real HTTP is a test failure."""

    def blocked(*args, **kwargs):
        raise AssertionError("External HTTP must be mocked in tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


@pytest.fixture
def test_app(tmp_path) -> FastAPI:
    database_path = tmp_path / "test.db"
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{database_path}",
        ai_provider="rules",
        chat_provider="demo",
        auto_route_threshold=0.90,
        log_level="WARNING",
    )
    return create_app(settings)


@pytest.fixture
def client(test_app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(test_app) as test_client:
        login = test_client.post(
            "/login",
            data={"username": "review.demo", "password": "review-demo-2026"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        session = test_client.get("/api/v1/session")
        assert session.status_code == 200
        test_client.headers["X-CSRF-Token"] = session.json()["csrf_token"]
        yield test_client


@pytest.fixture
def anonymous_client(test_app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(test_app) as test_client:
        yield test_client
