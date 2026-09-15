"""Extraction review is a separate citizen decision, before any final intake consent."""

import pytest
from playwright.sync_api import Page, expect

from app.config import Settings
from app.extraction_schemas import ExtractionProposal, ExtractionRequest
from app.services.chat_extraction import DEMO_TEXT, ExtractionRunner

from .conftest import ReportApp
from .test_citizen_chat import click_turn, send_message


@pytest.fixture
def extraction_page(isolated_page: Page, report_app: ReportApp, monkeypatch) -> Page:
    runner = ExtractionRunner(
        Settings(_env_file=None, app_env="test", chat_extraction_provider="demo")
    )
    monkeypatch.setattr(report_app.app.state, "extraction_runner", runner)
    isolated_page.goto(report_app.url + "/minwon/new")
    expect(isolated_page.get_by_role("button", name="합성 예시 넣어 보기")).to_be_enabled()
    return isolated_page


def test_preview_evidence_resume_mobile_confirmation_and_original_content(
    extraction_page: Page, report_app: ReportApp
) -> None:
    page = extraction_page
    page.get_by_role("button", name="합성 예시 넣어 보기").click()
    expect(page.locator("#chat-message")).to_have_value(DEMO_TEXT)
    click_turn(page, page.get_by_role("button", name="메시지 보내기", exact=True))
    panel = page.locator("[data-chat-extraction]")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("합성 예시 · 확인 전")
    expect(page.locator("#chat-extraction-title")).to_be_focused()
    expect(page.locator("[data-chat-composer]")).to_be_hidden()
    expect(page.locator("[data-chat-choices]")).to_be_hidden()
    panel.locator("summary").first.click()
    expect(panel.locator("blockquote").first).to_have_text("수리를 요청하고 싶어요.")
    page.reload()
    expect(panel).to_be_visible()
    page.set_viewport_size({"width": 320, "height": 900})
    page.get_by_role("radio", name="아주 크게", exact=True).check()
    assert panel.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
    bounds = panel.bounding_box()
    assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 321
    assert page.locator("html").evaluate(
        "element => element.scrollWidth <= element.clientWidth + 1"
    )
    accept = page.get_by_role("button", name="맞아요, 이 내용으로 계속", exact=True)
    accept.focus()
    with page.expect_response(
        lambda response: response.url.endswith("/minwon/chat/turn")
    ) as pending:
        accept.press("Enter")
    assert pending.value.status == 200
    expect(panel).to_be_hidden()
    expect(page.locator("#chat-question-title")).to_contain_text("가로등에 적힌 번호")
    click_turn(page, page.get_by_role("button", name="추가 질문은 여기까지 할게요"))
    expect(page.locator("[data-draft-content]")).to_have_text(DEMO_TEXT)
    expect(page.locator("[data-draft-location]")).to_have_text("합성 별빛공원 동문 앞")
    expect(page.locator("[data-chat-confirm]")).to_be_disabled()
    page.locator("[data-chat-answers] summary").click()
    expect(page.locator("[data-chat-answer-list]")).to_contain_text(
        "처음 내용에서 확인: 어제 저녁부터"
    )
    expect(page).to_have_url(report_app.url + "/minwon/new")


def test_changed_configuration_disables_accept_and_manual_choice_remains_available(
    extraction_page: Page, report_app: ReportApp
) -> None:
    page = extraction_page
    send_message(page, DEMO_TEXT)
    report_app.app.state.extraction_runner.settings.chat_extraction_provider = "off"
    page.reload()
    expect(page.locator("[data-chat-extraction]")).to_be_visible()
    expect(page.get_by_role("button", name="맞아요, 이 내용으로 계속")).to_be_disabled()
    expect(page.locator("[data-chat-extraction-help]")).to_contain_text("정리 기준이 바뀌었어요")
    click_turn(page, page.get_by_role("button", name="직접 선택할게요", exact=True))
    expect(page.locator("[data-chat-extraction]")).to_be_hidden()
    expect(page.get_by_role("button", name="민원으로 접수할게요", exact=True)).to_be_enabled()
    expect(page.locator("[data-chat-history]")).to_contain_text(DEMO_TEXT)


@pytest.mark.parametrize("answer_source", ["typed", "already_described", "extracted"])
def test_known_and_skipped_answers_survive_resume_edit_and_same_topic(
    extraction_page: Page, report_app: ReportApp, answer_source: str
) -> None:
    page = extraction_page
    if answer_source == "extracted":
        send_message(page, DEMO_TEXT)
        click_turn(page, page.get_by_role("button", name="맞아요, 이 내용으로 계속", exact=True))
        expected_time = "처음 내용에서 확인: 어제 저녁부터"
    else:
        page.locator("[data-chat-topics] summary").click()
        click_turn(page, page.get_by_role("button", name="가로등이 안 켜져요", exact=True))
        send_message(page, DEMO_TEXT)
        send_message(page, "합성 별빛공원 동문 앞")
        if answer_source == "typed":
            send_message(page, "어제 저녁부터")
            expected_time = "어제 저녁부터"
        else:
            click_turn(page, page.get_by_role("button", name="처음 내용에 이미 적었어요"))
            expected_time = "처음 내용에 설명함"

    expect(page.locator("#chat-question-title")).to_contain_text("가로등에 적힌 번호")
    click_turn(page, page.get_by_role("button", name="잘 모르겠어요 · 건너뛰기"))

    def check_review(label: str = "건너뜀") -> None:
        expect(page.locator("[data-chat-review]")).to_be_visible()
        expect(page.locator("[data-chat-question-panel]")).to_be_hidden()
        answers = page.locator("[data-chat-answers]")
        if answers.get_attribute("open") is None:
            answers.locator("summary").click()
        time_row = answers.locator(".chat-answer-row").filter(has_text="발견 시점")
        label_row = answers.locator(".chat-answer-row").filter(has_text="시설 표지")
        expect(time_row.locator("p")).to_have_text(expected_time)
        expect(label_row.locator("p")).to_have_text(label)
        expect(page.locator("[data-chat-consent]")).not_to_be_checked()
        expect(page.locator("[data-chat-confirm]")).to_be_disabled()
        expect(page).to_have_url(report_app.url + "/minwon/new")

    check_review()
    page.reload()
    check_review()
    page.locator("[data-chat-edit]").click()
    page.locator("#chat-edit-title").fill("합성 공원 가로등 확인 요청")
    page.locator("#chat-edit-location").fill("합성 별빛공원 서문 앞")
    click_turn(page, page.get_by_role("button", name="수정 내용 저장", exact=True))
    check_review()
    expected_content = DEMO_TEXT
    if answer_source == "typed":
        expected_content += "\n\n[추가로 알려준 내용]\n발견 시점: 어제 저녁부터"
    expect(page.locator("[data-draft-content]")).to_have_text(expected_content)
    page.locator("[data-chat-topics] summary").click()
    click_turn(page, page.get_by_role("button", name="가로등이 안 켜져요", exact=True))
    check_review()

    # Only an explicit correction reopens the skipped question, including after reload.
    click_turn(page, page.get_by_role("button", name="시설 표지 답변 수정", exact=True))
    page.reload()
    expect(page.locator("#chat-question-title")).to_contain_text("가로등에 적힌 번호")
    send_message(page, "합성 표지 A")
    check_review("합성 표지 A")
    page.reload()
    check_review("합성 표지 A")
    expect(page.locator("[data-draft-location]")).to_have_text("합성 별빛공원 서문 앞")


def test_information_hides_intake_fields_and_keeps_known_details_for_later_complaint(
    extraction_page: Page, monkeypatch
) -> None:
    source = "합성 별빛공원 동문 앞 가로등이 어제 저녁부터 꺼졌는데 신고 방법을 알고 싶어요."

    def manual_proposal(request: ExtractionRequest) -> ExtractionProposal:
        assert request.source_text == source
        return ExtractionProposal.model_validate(
            {
                "abstained": False,
                "reason": "supported",
                "purpose": {"value": "information", "quote": "신고 방법을 알고 싶어요."},
                "topic": {"template_id": "lighting", "quote": "가로등"},
                "location": {"value": "합성 별빛공원 동문 앞", "quote": "합성 별빛공원 동문 앞"},
                "answers": [
                    {
                        "field_id": "observed_time",
                        "value": "어제 저녁부터",
                        "quote": "어제 저녁부터",
                    }
                ],
            }
        )

    # A test-only proposal; the shipped demo still supports only its original fixed example.
    monkeypatch.setattr("app.services.chat_extraction.synthetic_proposal", manual_proposal)
    page = extraction_page
    send_message(page, source)
    expect(page.locator("[data-chat-extraction]")).to_contain_text("정보 알아보기")
    click_turn(page, page.get_by_role("button", name="맞아요, 이 내용으로 계속", exact=True))
    expect(page.locator("[data-chat-question-panel]")).to_be_hidden()
    expect(page.locator("[data-chat-answers]")).to_be_hidden()
    expect(page.locator("[data-chat-review]")).to_be_hidden()
    expect(page.locator("#chat-message")).to_be_focused()
    expect(page.locator("#chat-message")).to_have_attribute(
        "placeholder", "다른 궁금한 점이나 불편한 일을 적어 주세요."
    )
    page.reload()
    expect(page.locator("[data-chat-answers]")).to_be_hidden()
    page.locator("[data-chat-topics] summary").click()
    expect(page.get_by_role("button", name="가로등이 안 켜져요", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    click_turn(page, page.get_by_role("button", name="민원으로 접수할게요", exact=True))
    expect(page.locator("#chat-question-title")).to_contain_text("가로등에 적힌 번호")
    click_turn(page, page.get_by_role("button", name="추가 질문은 여기까지 할게요"))
    expect(page.locator("[data-draft-content]")).to_have_text(source)
    expect(page.locator("[data-draft-location]")).to_have_text("합성 별빛공원 동문 앞")
    page.locator("[data-chat-answers] summary").click()
    expect(page.locator("[data-chat-answer-list]")).to_contain_text(
        "처음 내용에서 확인: 어제 저녁부터"
    )
    expect(page.locator("[data-chat-confirm]")).to_be_disabled()
