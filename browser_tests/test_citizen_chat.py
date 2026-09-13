"""Protect unsent citizen input without storing it or interfering with confirmed intake."""

import json
from pathlib import Path

from PIL import Image
from playwright.sync_api import Dialog, Locator, Page, Route, expect

from .conftest import ReportApp

DESCRIPTION = "가상 데모공원 가로등이 어제부터 꺼져 있어요."
UNSENT = "아직 보내지 않은 합성 추가 설명입니다."


def track_dialogs(page: Page) -> list[str]:
    seen: list[str] = []

    def dismiss(dialog: Dialog) -> None:
        seen.append(dialog.type)
        dialog.dismiss()

    page.on("dialog", dismiss)
    return seen


def click_turn(page: Page, button: Locator) -> None:
    with page.expect_response(
        lambda response: response.url.endswith("/minwon/chat/turn"), timeout=15_000
    ) as pending:
        button.click()
    assert pending.value.status == 200
    expect(page.locator("[data-chat-history]")).to_have_attribute("aria-busy", "false")


def send_message(page: Page, text: str) -> None:
    page.locator("#chat-message").fill(text)
    click_turn(page, page.get_by_role("button", name="메시지 보내기", exact=True))
    expect(page.locator("#chat-message")).to_have_value("")


def open_review(page: Page) -> None:
    send_message(page, DESCRIPTION)
    click_turn(page, page.get_by_role("button", name="민원으로 접수할게요", exact=True))
    send_message(page, "가상 데모공원 정문 앞")
    expect(page.locator("[data-chat-review]")).to_be_visible()


def test_unsent_text_cancel_keyboard_and_mobile_then_explicit_leave(
    citizen_page: Page, report_app: ReportApp
) -> None:
    page = citizen_page
    native_dialogs = track_dialogs(page)
    send_message(page, DESCRIPTION)
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("radio", name="아주 크게", exact=True).check()
    page.locator("#chat-message").fill(UNSENT)
    home = page.get_by_role("link", name="성남 생활민원 홈", exact=True)
    home.focus()
    home.press("Enter")
    dialog = page.get_by_role("dialog", name="작성을 멈추고 이동할까요?", exact=True)
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("아직 보내지 않은 글은 사라져요.")
    expect(dialog.get_by_role("button", name="계속 작성하기", exact=True)).to_be_focused()
    bounds = dialog.bounding_box()
    assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 391
    assert dialog.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(home).to_be_focused()
    expect(page.locator("#chat-message")).to_have_value(UNSENT)
    home.click()
    dialog.get_by_role("button", name="계속 작성하기", exact=True).click()
    expect(page.locator("#chat-message")).to_have_value(UNSENT)
    home.click()
    dialog.get_by_role("button", name="이동하기", exact=True).click()
    page.wait_for_url(report_app.url + "/")
    assert not native_dialogs  # Explicitly confirmed navigation must not ask a second time.
    page.goto(report_app.url + "/minwon/new")
    expect(page.locator("#chat-message")).to_be_enabled()
    expect(page.locator("#chat-message")).to_have_value("")
    expect(page.locator("[data-chat-history]")).to_contain_text(DESCRIPTION)
    expect(page.locator("[data-chat-history]")).not_to_contain_text(UNSENT)


def test_tab_close_and_reload_preserve_unsent_text_and_sent_text_needs_no_warning(
    citizen_page: Page,
) -> None:
    page = citizen_page
    native_dialogs = track_dialogs(page)
    page.locator("#chat-message").click()
    page.locator("#chat-message").fill(DESCRIPTION)
    with page.expect_event("dialog", timeout=5_000):
        page.close(run_before_unload=True)
    assert native_dialogs == ["beforeunload"]
    assert not page.is_closed()
    expect(page.locator("#chat-message")).to_have_value(DESCRIPTION)
    with page.expect_event("dialog", timeout=5_000):
        page.evaluate("() => { setTimeout(() => window.location.reload(), 0); }")
    assert native_dialogs == ["beforeunload", "beforeunload"]
    expect(page.locator("#chat-message")).to_have_value(DESCRIPTION)
    click_turn(page, page.get_by_role("button", name="메시지 보내기", exact=True))
    expect(page.locator("#chat-message")).to_have_value("")
    with page.expect_event("close", timeout=5_000):
        page.close(run_before_unload=True)
    assert native_dialogs == ["beforeunload", "beforeunload"]


def test_unsaved_review_edit_is_preserved_but_saved_and_unchanged_edits_can_leave(
    citizen_page: Page, report_app: ReportApp
) -> None:
    page = citizen_page
    native_dialogs = track_dialogs(page)
    open_review(page)
    page.locator("[data-chat-edit]").click()
    title = page.get_by_role("textbox", name="민원 제목", exact=True)
    title.fill("가상 공원 서쪽 가로등 확인 요청")
    page.get_by_role("link", name="성남 생활민원 홈", exact=True).click()
    dialog = page.locator("[data-chat-leave-dialog]")
    expect(dialog).to_contain_text("저장하지 않은 수정 내용은 사라져요.")
    expect(dialog).not_to_contain_text("아직 보내지 않은 글은 사라져요.")
    dialog.get_by_role("button", name="계속 작성하기", exact=True).click()
    expect(title).to_have_value("가상 공원 서쪽 가로등 확인 요청")
    click_turn(page, page.get_by_role("button", name="수정 내용 저장", exact=True))
    expect(page.locator("[data-chat-consent]")).not_to_be_checked()
    expect(page.locator("[data-chat-confirm]")).to_be_disabled()
    page.get_by_role("link", name="성남 생활민원 홈", exact=True).click()
    page.wait_for_url(report_app.url + "/")
    page.goto(report_app.url + "/minwon/new")
    expect(page.locator("[data-draft-title]")).to_have_text("가상 공원 서쪽 가로등 확인 요청")
    page.locator("[data-chat-edit]").click()
    # Merely opening the edit form does not mean anything needs saving.
    page.get_by_role("link", name="성남 생활민원 홈", exact=True).click()
    page.wait_for_url(report_app.url + "/")
    assert not native_dialogs


def test_photo_leave_cancel_removal_and_confirmed_intake(
    citizen_page: Page, report_app: ReportApp, tmp_path: Path
) -> None:
    page = citizen_page
    native_dialogs = track_dialogs(page)
    open_review(page)
    photo = tmp_path / "합성 초록 사각형.png"
    Image.new("RGB", (24, 24), "green").save(photo)
    page.locator("#chat-photo-input").set_input_files(photo, timeout=5_000)
    expect(page.locator("[data-chat-photo-count]")).to_have_text("1 / 3장")
    page.get_by_role("link", name="성남 생활민원 홈", exact=True).click()
    dialog = page.locator("[data-chat-leave-dialog]")
    expect(dialog).to_contain_text("선택한 사진 1장은 다시 골라야 해요.")
    dialog.get_by_role("button", name="계속 작성하기", exact=True).click()
    expect(page.locator("[data-chat-photo-list] img")).to_have_count(1)
    page.get_by_role("button", name="사진 1 삭제", exact=True).click()
    page.get_by_role("link", name="성남 생활민원 홈", exact=True).click()
    page.wait_for_url(report_app.url + "/")
    page.goto(report_app.url + "/minwon/new")
    expect(page.locator("[data-chat-review]")).to_be_visible()
    page.locator("#chat-photo-input").set_input_files(photo, timeout=5_000)
    expect(page.locator("[data-chat-confirm]")).to_be_disabled()
    page.locator("[data-chat-consent]").check()
    page.locator("[data-chat-confirm]").click()
    expect(page.get_by_role("heading", name="민원이 접수되었어요.", exact=True)).to_be_visible(
        timeout=20_000
    )
    expect(page.locator(".receipt-card")).to_contain_text("사진 1장도 함께 접수했어요.")
    assert not native_dialogs


def test_unconfirmed_request_result_is_protected_and_retry_keeps_request_id(
    citizen_page: Page, report_app: ReportApp
) -> None:
    page = citizen_page
    native_dialogs = track_dialogs(page)
    attempts: list[dict] = []

    def fail_once(route: Route) -> None:
        attempts.append(json.loads(route.request.post_data or "{}"))
        if len(attempts) == 1:
            route.fulfill(status=503, json={"message": "합성 오류: 응답을 확인하지 못했어요."})
        else:
            route.continue_()

    page.route("**/minwon/chat/turn", fail_once)
    page.get_by_role("button", name="생활 불편 알리기", exact=True).click()
    expect(page.locator("[data-chat-error]")).to_be_visible()
    expect(page.locator("#chat-message")).to_have_value("")
    page.get_by_role("link", name="성남 생활민원 홈", exact=True).click()
    dialog = page.locator("[data-chat-leave-dialog]")
    expect(dialog).to_contain_text("전송 결과를 아직 확인하지 못했어요.")
    expect(dialog.locator("li")).to_have_count(1)
    dialog.get_by_role("button", name="계속 작성하기", exact=True).click()
    click_turn(page, page.get_by_role("button", name="다시 시도", exact=True))
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    page.get_by_role("link", name="성남 생활민원 홈", exact=True).click()
    page.wait_for_url(report_app.url + "/")
    assert not native_dialogs
