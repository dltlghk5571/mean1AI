"""Real staff forms in an isolated browser, using only synthetic confirmed candidate fixtures."""

import os
from pathlib import Path

from playwright.sync_api import Page, Route, expect

from tests.test_incidents import MESSAGE, REASON, TITLE, seed_pair

from .conftest import ReportApp


def open_pair(page: Page, report_app: ReportApp) -> list[str]:
    ids = seed_pair(report_app.app)
    page.goto(report_app.url + f"/complaints/{ids[1]}")
    page.get_by_role("link", name="사건 연결 검토", exact=True).click()
    expect(page.get_by_role("heading", name="같은 현장 문제인가요?", exact=True)).to_be_visible()
    return ids


def fill_create(page: Page) -> None:
    page.get_by_role("textbox", name="담당자용 사건 이름", exact=True).fill(TITLE)
    page.get_by_role("textbox", name="검토 사유", exact=True).fill(REASON)
    page.get_by_role("checkbox").check()


def test_group_link_publish_and_split_using_staff_forms(page: Page, report_app: ReportApp) -> None:
    ids = open_pair(page, report_app)
    page.set_viewport_size({"width": 390, "height": 844})
    fill_create(page)
    shell = page.locator(".incident-shell")
    assert shell.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
    page.get_by_role("button", name="사건 만들고 두 민원 연결", exact=True).click()
    expect(page.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
    expect(page.locator(".incident-heading")).to_contain_text("2건")
    incident_id = page.url.split("/")[-1]
    publication = page.locator('form[action$="/published"]')
    publication.get_by_role("textbox", name="시민에게 공개할 안내", exact=True).fill(MESSAGE)
    publication.get_by_role("textbox", name="검토 사유", exact=True).fill(REASON)
    publication.get_by_role("checkbox").check()
    publication.get_by_role("button", name="공통 안내 공개", exact=True).click()
    expect(page.locator("[data-publication]")).to_contain_text(MESSAGE)
    if os.environ.get("SEONGNAM_CAPTURE_PREVIEW") == "1":
        artifacts = Path(__file__).resolve().parents[1] / ".local/browser-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(artifacts / "incident-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 1000})
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(artifacts / "incident-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})

    page.goto(
        report_app.url + f"/staff/incidents/prepare?complaint_id={ids[2]}&candidate_id={ids[0]}"
    )
    expect(page.get_by_role("heading", name="기존 사건에 연결", exact=True)).to_be_visible()
    page.get_by_role("textbox", name="검토 사유", exact=True).fill(REASON)
    page.get_by_role("checkbox").check()
    page.get_by_role("button", name="이 사건에 연결", exact=True).click()
    expect(page.locator(".incident-heading")).to_contain_text("3건")
    expect(page.locator("[data-no-publication]")).to_be_visible()
    assert page.url.endswith(incident_id)

    member = page.locator(".incident-member").filter(
        has=page.get_by_role("link", name="합성 조명 제보 0", exact=True)
    )
    member.locator("summary").click()
    member.get_by_role("textbox", name="검토 사유", exact=True).fill(
        "다른 시설임을 확인한 합성 분리 사유입니다."
    )
    member.get_by_role("checkbox").check()
    member.get_by_role("button", name="선택한 민원 분리", exact=True).click()
    expect(page.locator(".incident-heading")).to_contain_text("2건")
    expect(page.locator(".incident-member")).to_have_count(2)
    expect(page.locator(".incident-history")).to_contain_text("민원 분리")


def test_stale_form_keeps_reviewed_text_and_allows_recovery(
    page: Page, report_app: ReportApp
) -> None:
    open_pair(page, report_app)
    fill_create(page)

    def stale(route: Route) -> None:
        route.fulfill(
            status=409, json={"message": "다른 작업이 먼저 반영되었습니다. 최신 상태를 확인하세요."}
        )

    page.route("**/staff/incidents/create", stale)
    button = page.get_by_role("button", name="사건 만들고 두 민원 연결", exact=True)
    button.click()
    error = page.locator("[data-incident-error]")
    expect(error).to_be_visible()
    expect(error).to_be_focused()
    expect(error).to_contain_text("최신 상태")
    expect(page.get_by_role("textbox", name="담당자용 사건 이름", exact=True)).to_have_value(TITLE)
    expect(page.get_by_role("textbox", name="검토 사유", exact=True)).to_have_value(REASON)
    expect(button).to_be_enabled()
    page.unroute("**/staff/incidents/create", stale)
    button.click()
    expect(page.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
