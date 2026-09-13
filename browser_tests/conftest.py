"""Isolated loopback app + temporary Chromium profile, independent of the user's browser."""

import ctypes
import socket
import sys
import threading
import time
from collections.abc import Generator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from playwright.sync_api import Browser, Page, Route, expect, sync_playwright

from app.config import Settings
from app.main import create_app
from app.services.citizen import CitizenRateLimiter

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ReportApp:
    url: str
    app: FastAPI


@pytest.fixture(scope="session")
def report_app(tmp_path_factory: pytest.TempPathFactory) -> Generator[ReportApp, None, None]:
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleTitleW("Seongnam - civic browser tests")
    database = tmp_path_factory.mktemp("civic-browser") / "test.db"
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            database_url=f"sqlite:///{database.as_posix()}",
            ai_provider="rules",
            chat_provider="agent_demo",
            log_level="WARNING",
        )
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_level="warning",
                timeout_graceful_shutdown=3,
            )
        )
        worker = threading.Thread(
            target=server.run,
            kwargs={"sockets": [listener]},
            name="Seongnam civic browser test server",
            daemon=True,
        )
        worker.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not worker.is_alive() or time.monotonic() >= deadline:
                    pytest.fail("The isolated browser test server did not start within 15 seconds")
                time.sleep(0.05)
            yield ReportApp(f"http://127.0.0.1:{port}", app)
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            if worker.is_alive():
                server.force_exit = True
                worker.join(timeout=3)
            assert not worker.is_alive(), "The isolated browser test server did not stop"


@pytest.fixture(scope="session")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=20_000)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture(autouse=True)
def block_app_external_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args, **kwargs):
        raise AssertionError("The browser test must not make external app HTTP calls")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


@pytest.fixture
def isolated_page(
    browser: Browser,
    report_app: ReportApp,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Page, None, None]:
    # Tests share a loopback address, but each scenario needs its own request budget.
    monkeypatch.setattr(report_app.app.state, "citizen_limiter", CitizenRateLimiter())
    # No channel, user_data_dir, persistent context, saved credentials, or CDP connection.
    context = browser.new_context(
        accept_downloads=True,
        viewport={"width": 1280, "height": 900},
        service_workers="block",
    )
    context.set_default_timeout(10_000)
    errors: list[str] = []
    external_requests: list[str] = []

    def only_test_origin(route: Route) -> None:
        origin = urlsplit(report_app.url)
        target = urlsplit(route.request.url)
        if target.scheme == origin.scheme and target.netloc == origin.netloc:
            route.continue_()
        else:
            external_requests.append(target.netloc)
            route.abort()

    context.route("**/*", only_test_origin)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        yield page
        assert not errors, f"Unexpected browser script error: {errors}"
        assert not external_requests, "The page attempted an external request"
    finally:
        # Synthetic input only. Keep a screenshot of failures; never export cookies or storage.
        report = getattr(request.node, "report_call", None)
        if report is not None and report.failed:
            artifact = ROOT / ".local/browser-artifacts" / f"{request.node.name}.png"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            # A screenshot failure must not hide the test failure or prevent cleanup.
            with suppress(Exception):
                page.screenshot(path=str(artifact), full_page=True, timeout=3_000)
        context.close()


@pytest.fixture
def page(isolated_page: Page, report_app: ReportApp) -> Page:
    isolated_page.goto(report_app.url + "/staff/source-reports")
    isolated_page.get_by_role("textbox", name="아이디", exact=True).fill("review.demo")
    isolated_page.get_by_role("textbox", name="비밀번호", exact=True).fill("review-demo-2026")
    isolated_page.get_by_role("button", name="로그인", exact=True).click()
    isolated_page.wait_for_url(report_app.url + "/staff/source-reports")
    return isolated_page


@pytest.fixture
def citizen_page(isolated_page: Page, report_app: ReportApp) -> Page:
    isolated_page.goto(report_app.url + "/minwon/new")
    expect(isolated_page.locator("#chat-message")).to_be_enabled()
    return isolated_page


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    report = yield
    setattr(item, f"report_{report.when}", report)
    return report
