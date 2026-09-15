"""Officer comparison pages use synthetic data and mocked HTTP in an isolated browser."""

import os
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect

from app.models import Complaint
from app.services.incident_comparison import IncidentComparisonRunner
from tests.test_incident_comparison import SUMMARY, configuration, reply_for
from tests.test_incidents import seed_pair

from .conftest import ReportApp


def test_offline_comparison_and_explicit_demo_on_mobile(page: Page, report_app: ReportApp) -> None:
    ids = seed_pair(report_app.app)
    original = report_app.app.state.incident_comparator
    try:
        page.goto(report_app.url + f"/complaints/{ids[1]}")
        page.get_by_role("link", name="같은 사건 비교", exact=True).click()
        expect(page.locator("[data-comparison-off]")).to_be_visible()
        expect(page.get_by_role("button", name="모델에 비교 요청", exact=True)).to_be_disabled()
        report_app.app.state.incident_comparator = IncidentComparisonRunner(
            configuration(incident_compare_provider="demo"),
            report_app.app.state.session_factory,
        )
        page.reload()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator("[data-comparison-demo]")).to_contain_text("합성 응답 시연")
        page.get_by_role("button", name="합성 비교 응답 확인", exact=True).click()
        expect(page.locator("[data-comparison-result]")).to_contain_text("판단 보류")
        expect(page.locator("[data-comparison-result]")).to_contain_text(
            "실제 모델 판단은 수행하지"
        )
        shell = page.locator(".incident-shell")
        assert shell.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        if os.environ.get("SEONGNAM_CAPTURE_PREVIEW") == "1":
            artifacts = Path(__file__).resolve().parents[1] / ".local/browser-artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(artifacts / "comparison-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1280, "height": 1000})
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(artifacts / "comparison-desktop.png"), full_page=True)
        page.get_by_role("link", name="민원으로 돌아가 판단하기", exact=True).click()
        expect(page.get_by_role("link", name="사건 연결 검토", exact=True)).to_be_visible()
    finally:
        report_app.app.state.incident_comparator = original


def test_source_quotes_stale_result_and_model_failure_recovery(
    page: Page, report_app: ReportApp
) -> None:
    ids = seed_pair(report_app.app)
    original = report_app.app.state.incident_comparator
    runner = IncidentComparisonRunner(
        configuration(),
        report_app.app.state.session_factory,
        transport=httpx.MockTransport(reply_for),
    )
    report_app.app.state.incident_comparator = runner
    try:
        page.goto(report_app.url + f"/staff/incident-comparisons/{ids[1]}/{ids[0]}")
        page.get_by_role("button", name="모델에 비교 요청", exact=True).click()
        expect(page.locator("[data-comparison-result]")).to_contain_text(SUMMARY)
        expect(page.get_by_role("heading", name="입력에서 확인한 근거 문장")).to_be_visible()
        expect(page.locator("blockquote")).to_have_count(2)
        with report_app.app.state.session_factory() as db:
            item = db.get(Complaint, ids[1])
            assert item
            item.redacted_content += " 합성 추가 확인: 옆 시설인지 확인이 필요합니다."
            db.commit()
        page.reload()
        expect(page.locator("[data-comparison-stale]")).to_contain_text("이전 결과를 숨겼습니다")
        expect(page.locator("[data-comparison-result]")).to_have_count(0)
        runner.model.transport = httpx.MockTransport(lambda request: reply_for(request, status=503))
        page.get_by_role("button", name="모델에 비교 요청", exact=True).click()
        expect(page.locator("[data-comparison-failed]")).to_be_visible()
        expect(page.get_by_role("button", name="모델에 비교 요청", exact=True)).to_be_enabled()
        runner.model.transport = httpx.MockTransport(reply_for)
        page.get_by_role("button", name="모델에 비교 요청", exact=True).click()
        expect(page.locator("[data-comparison-result]")).to_contain_text("같은 사건 가능성")
    finally:
        report_app.app.state.incident_comparator = original
