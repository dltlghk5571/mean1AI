"""Exercise real file inputs, HTTP, DOM rendering, and browser-generated downloads."""

import hashlib
import json
from pathlib import Path

from playwright.sync_api import Page, expect
from sqlalchemy import func, select

from app.models import ServiceCatalogReview, ServiceCatalogVersion

from .conftest import ROOT, ReportApp

DEMO = ROOT / "app/data/collection_report_demo.json"


def choose_file(page: Page, path: Path, *, keyboard: bool = False) -> None:
    with page.expect_file_chooser(timeout=10_000) as choice:
        if keyboard:
            page.get_by_label("JSON 파일 선택", exact=True).focus()
            page.get_by_label("JSON 파일 선택", exact=True).press("Enter")
        else:
            page.locator('label[for="sr-file"]').click()
    choice.value.set_files(path, timeout=10_000)


def upload(page: Page, path: Path, *, status: int = 200, keyboard: bool = False) -> None:
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/v1/source-reports/preview")
            and response.request.method == "POST"
        ),
        timeout=15_000,
    ) as pending:
        choose_file(page, path, keyboard=keyboard)
    response = pending.value
    assert response.status == status
    assert response.request.post_data_buffer == path.read_bytes()
    expect(page.locator("[data-source-report]")).to_have_attribute("aria-busy", "false")
    expect(page.locator("#sr-file")).to_have_value("")
    expect(page.locator("#sr-file")).to_be_enabled()
    # no-store responses need not remain in Chromium's inspector cache after the page consumes them.
    # Verify outcomes through the visible page and the actual exported file instead of response.json.


def test_file_upload_and_download_preserve_all_pending_evidence(
    page: Page, report_app: ReportApp, tmp_path: Path
) -> None:
    path = tmp_path / "검수 예시 보고서.json"
    path.write_bytes(DEMO.read_bytes())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    inputs = json.loads(path.read_text("utf-8"))
    upload(page, path, keyboard=True)
    expect(page.locator("[data-report-result]")).to_be_visible()
    expect(page.locator("[data-report-origin]")).to_contain_text(path.name)
    expect(page.locator("[data-report-mismatch]")).to_be_hidden()
    page.get_by_text("보고서 식별 정보", exact=True).click()
    expect(page.locator("[data-report-hash]")).to_have_text(digest)
    expect(page.locator(".sr-task")).to_have_count(6)

    page.get_by_role("button", name="부서 표기 1", exact=True).click()
    expect(page.locator(".sr-task")).to_have_count(1)
    page.get_by_role("button", name="페이지 근거 보기", exact=True).click()
    expect(page.locator(".sr-comparison")).to_contain_text("가상업무과")
    expect(page.locator(".sr-comparison")).to_contain_text("가상지원과")
    expect(page.get_by_role("link", name="원본 페이지 열기 ↗", exact=True)).to_have_count(0)

    with page.expect_download(timeout=10_000) as pending_download:
        page.get_by_role("button", name="확인할 일 내려받기", exact=True).click()
    download = pending_download.value
    assert download.failure() is None
    assert download.suggested_filename == f"source-review-seongnam-welfare-{digest[:12]}.json"
    output = tmp_path / "downloaded-review.json"
    download.save_as(output)
    exported = json.loads(output.read_text("utf-8"))
    assert exported["schema_version"] == "1"
    assert exported["report_sha256"] == digest
    assert exported["source_id"] == "seongnam-welfare"
    assert exported["review_status"] == "pending"
    # Export must retain all categories even while one department task is visible.
    assert len(exported["tasks"]) == 6
    assert sorted(task["kind"] for task in exported["tasks"]) == [
        "department",
        "missing",
        "missing",
        "source",
        "source",
        "source",
    ]
    assert len(exported["pages"]) == 2
    for task in exported["tasks"]:
        assert task["status"] == "pending" and task["source_url"] is None
        if task["page_index"] is not None:
            source = exported["pages"][task["page_index"]]
            assert source["index"] == task["page_index"]
            assert source["input_sha256"] == inputs["pages"][task["page_index"]]["input_sha256"]
    assert all(
        "body" not in source and source["source_url"] is None for source in exported["pages"]
    )

    # Selecting the identical file again must trigger a new upload and reset the active filter.
    upload(page, path)
    expect(page.locator(".sr-task")).to_have_count(6)
    page.get_by_role("button", name="보고서 닫기", exact=True).click()
    expect(page.locator("[data-report-result]")).to_be_hidden()
    expect(page.locator("[data-report-empty]")).to_be_visible()
    with report_app.app.state.session_factory() as db:
        assert db.scalar(select(func.count(ServiceCatalogVersion.version))) == 0
        assert db.scalar(select(func.count(ServiceCatalogReview.id))) == 0


def test_invalid_file_hides_previous_result_then_changed_summary_recovers(
    page: Page, tmp_path: Path
) -> None:
    upload(page, DEMO)
    invalid = tmp_path / "손상된 보고서.json"
    invalid.write_text('{"schema_version":"99"}', encoding="utf-8")
    for _ in range(2):
        upload(page, invalid, status=422)
        expect(page.get_by_role("alert")).to_contain_text("보고서를 열지 못했습니다")
        expect(page.locator("[data-report-result]")).to_be_hidden()
        expect(page.locator(".sr-task")).to_have_count(0)
    modified = json.loads(DEMO.read_text("utf-8"))
    modified["completed"] = True
    modified["remaining_links"] = 0
    modified["reconciliation"]["missing_detail_ids"] = []
    changed = tmp_path / "집계가 변경된 보고서.json"
    changed.write_text(json.dumps(modified, ensure_ascii=False), encoding="utf-8")
    upload(page, changed)
    expect(page.locator("[data-report-mismatch]")).to_be_visible()
    expect(page.locator("[data-report-boundary]")).to_contain_text("아직 확보하거나 확인할 자료")
    expect(page.locator(".sr-task")).to_have_count(6)


def test_oversize_file_stays_local_and_identifier_error_does_not_display_input(
    page: Page, tmp_path: Path
) -> None:
    upload(page, DEMO)
    preview_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            preview_requests.append(request.url)
            if request.url.endswith("/api/v1/source-reports/preview")
            else None
        ),
    )
    oversized = tmp_path / "용량 초과.json"
    oversized.write_bytes(b" " * 8_000_001)
    choose_file(page, oversized)
    expect(page.get_by_role("alert")).to_contain_text("8MB")
    expect(page.locator("[data-report-result]")).to_be_hidden()
    expect(page.locator("#sr-file")).to_have_value("")
    assert not preview_requests

    direct_id = "010-1111-2222"  # Explicitly synthetic, matching existing security fixtures.
    content = json.loads(DEMO.read_text("utf-8"))
    content["pages"][1]["body_contacts"] = [f"합성 {direct_id}"]
    private = tmp_path / "합성 식별자 포함.json"
    private.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    upload(page, private, status=422)
    expect(page.get_by_role("alert")).to_contain_text("전화번호·이메일·주민등록번호")
    expect(page.locator("body")).not_to_contain_text(direct_id)
    expect(page.locator("[data-report-result]")).to_be_hidden()
    upload(page, DEMO)
    expect(page.locator("[data-report-result]")).to_be_visible()
