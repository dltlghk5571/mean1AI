import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.collect_services import main
from app.collection_schemas import ExtractedPage
from app.services import service_collection as collection
from app.services.collection_report import build_report, collect_local_manifest
from app.services.collection_sources import ORGANIZATION_CODES, SOURCES, CollectionError
from app.services.service_collection import collect, extract_document, extract_page

FIXTURES = Path(__file__).parent / "fixtures"
LIST_HTML = (FIXTURES / "seongnam_welfare_list_synthetic.html").read_bytes()
DETAIL_HTML = (FIXTURES / "seongnam_welfare_detail_synthetic.html").read_bytes()
ORG_HTML = (FIXTURES / "seongnam_organization_synthetic.html").read_bytes()
WELFARE = SOURCES["seongnam-welfare"]
ORG = SOURCES["seongnam-organization"]
FIRST = f"{WELFARE.seed_url}/9900000001"
SECOND = f"{WELFARE.seed_url}/9900000002"


def report(pages: list[ExtractedPage], *, remaining: int = 0) -> dict:
    return build_report(
        WELFARE, pages, [], mode="local_manifest", visited=len(pages), remaining_links=remaining
    )


def complete_pages() -> list[ExtractedPage]:
    listing = extract_page(
        WELFARE,
        WELFARE.seed_url,
        LIST_HTML.replace(b'<a href="?curPage=2">2</a>', b""),
        synthetic=True,
    )
    first = extract_page(WELFARE, FIRST, DETAIL_HTML, synthetic=True)
    second_html = (
        DETAIL_HTML.decode()
        .replace("가상 생활지원", "가상 방문지원")
        .replace("가상복지국&gt;가상지원과&gt;가상안내팀", "가상돌봄과")
        .encode()
    )
    return [listing, first, extract_page(WELFARE, SECOND, second_html, synthetic=True)]


def manifest_file(tmp_path: Path, entries: list[dict[str, str]], **extra) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "source_id": WELFARE.id,
                "synthetic": True,
                "pages": entries,
                **extra,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_url_identity_accepts_only_observed_navigation_parameters() -> None:
    url = (
        FIRST
        + "?curPage=2&sortType=&cvlcptBizSn=9900000001&arrWlfCrrCyclCd="
        + "&srchText=&cntPerPage=9&board-input-text=2"
    )
    assert WELFARE.canonical_url(url) == FIRST
    assert WELFARE.canonical_url(WELFARE.seed_url + "?cntPerPage=12&curPage=2") == (
        WELFARE.canonical_url(WELFARE.seed_url + "?curPage=2&cntPerPage=12")
    )
    assert WELFARE.canonical_url(WELFARE.seed_url + "?curPage=1&cntPerPage=9") == WELFARE.seed_url
    assert (
        ORG.canonical_url(
            "https://www.seongnam.go.kr/pm04041101?orgSelect=orgSelect02&deptCode=38100720000"
        )
        == ORG.seed_url
    )


@pytest.mark.parametrize(
    "suffix",
    [
        "?srchText=private",
        "?arrWlfCrrCyclCd=1",
        "?token=secret",
        "?curPage=0",
        "?curPage=2&curPage=3",
        "?cntPerPage=500",
        "?cvlcptBizSn=9900000002",
        "?cvlcptBizSn=",
        "?sortType=latest",
        "?board-input-text=bad",
        "#fragment",
        "?curPage",
        "?curPage=1;token=secret",
    ],
)
def test_filtered_or_ambiguous_welfare_urls_are_rejected(suffix: str) -> None:
    assert WELFARE.canonical_url(FIRST + suffix) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://www.seongnam.go.kr/wf-pm020101",
        "https://www.seongnam.go.kr.evil.test/wf-pm020101",
        "https://user:secret@www.seongnam.go.kr/wf-pm020101",
        "https://127.0.0.1/wf-pm020101",
        "https://www.seongnam.go.kr/wf-pm020101/01",
        "https://www.seongnam.go.kr/download?file=1",
    ],
)
def test_structured_scope_rejects_other_origins_and_files(url: str) -> None:
    with pytest.raises(CollectionError, match="source_url"):
        extract_page(WELFARE, url, LIST_HTML)


@pytest.mark.parametrize(
    "query",
    [
        "deptCode=unknown&orgSelect=orgSelect02",
        "deptCode=38100720000&orgSelect=orgSelect01",
        "deptCode=38100720000",
        "deptCode=38100720000&orgSelect=orgSelect02&employee=person",
        "deptCode=38100720000&deptCode=38100730000&orgSelect=orgSelect02",
    ],
)
def test_organization_scope_requires_a_registered_code(query: str) -> None:
    assert ORG.canonical_url("https://www.seongnam.go.kr/pm04041101?" + query) is None


def test_listing_reads_cards_inside_form_and_literal_script_links() -> None:
    page = extract_page(WELFARE, WELFARE.seed_url, LIST_HTML, synthetic=True)
    assert page.page_kind == "welfare_list" and page.document is None
    assert page.records_seen == page.records_extracted == page.reported_total == 2
    assert page.extraction_complete
    assert [item.source_code for item in page.listing_items] == ["9900000001", "9900000002"]
    assert page.listing_items[0].displayed_department == "가상복지국>가상지원과>가상안내팀"
    assert "[전화번호]" in page.listing_items[0].summary
    assert page.discovered_links == [FIRST, SECOND, WELFARE.seed_url + "?curPage=2"]
    assert {item.group for item in page.welfare_filters} == {"생애주기", "가구상황", "관심주제"}
    assert page.welfare_filters[0].source_code == "srchCtgry_WLF_CRR_CYCL_7"
    assert "수집 제외" not in page.model_dump_json()
    with pytest.raises(CollectionError, match="listing_is_not"):
        extract_document(WELFARE, WELFARE.seed_url, LIST_HTML)


@pytest.mark.parametrize(
    "script",
    [
        "fn_move_form(9900000001);send_secret()",
        "fn_move_form(value)",
        "other(9900000001)",
    ],
)
def test_unrecognized_script_is_reported_as_a_missing_card(script: str) -> None:
    page = extract_page(
        WELFARE, WELFARE.seed_url, LIST_HTML.replace(b"fn_move_form(9900000001)", script.encode())
    )
    assert page.records_seen == 2 and page.records_extracted == 1
    assert not page.extraction_complete
    assert "listing_detail_link_unverified" in page.review_issues
    assert not report([page])["completed"]


def test_conflicting_direct_and_scripted_card_links_are_not_followed() -> None:
    html = LIST_HTML.replace(b'href="javascript:void(0)"', f'href="{SECOND}"'.encode())
    page = extract_page(WELFARE, WELFARE.seed_url, html)
    assert FIRST not in page.discovered_links and not page.extraction_complete


@pytest.mark.parametrize(
    "data",
    [
        b'<div id="contentLoad">Login required</div>',
        b"x" * 1_000_001,
        b"\xff\xfe",
        b'<div id="contentLoad"></div><div id="contentLoad"></div>',
        b'<div id="contentLoad">' + b"<div>" * 102,
    ],
    ids=["missing-layout", "oversize", "bad-encoding", "duplicate-root", "deep-html"],
)
def test_missing_changed_and_unbounded_structure_fails_closed(data: bytes) -> None:
    with pytest.raises(CollectionError):
        extract_page(WELFARE, WELFARE.seed_url, data)


def test_detail_separates_contact_labels_without_inferring_a_department_or_policy_date() -> None:
    page = extract_page(WELFARE, FIRST, DETAIL_HTML, input_kind="rendered_dom")
    assert page.document is not None and page.document.title == "가상 생활지원"
    assert page.body_contacts == ["가상복지국 > 가상업무과 > 가상업무팀"]
    assert page.footer_department == "가상복지국>가상지원과>가상안내팀"
    assert "department_labels_differ" in page.review_issues
    assert page.policy_year_mentions == [2025]
    assert (
        page.document.fetched_at is page.document.updated_at is page.document.published_at is None
    )
    assert page.document.retrieval_use == page.document.training_use == "unknown"
    assert page.input_sha256 == hashlib.sha256(DETAIL_HTML).hexdigest()
    assert page.input_sha256 != page.document.content_hash
    for excluded in (
        "가상안내팀",
        "첨부 원문 제외",
        "목록 버튼 제외",
        "스크립트 제외",
        "숨긴 내용 제외",
        "031-000",
    ):
        assert excluded not in page.document.text


def test_contact_heading_on_separate_line_and_missing_footer_are_reviewable() -> None:
    html = (
        DETAIL_HTML.decode()
        .replace('<b class="title-b">문의처</b>:', "<h4>문의처</h4>")
        .replace("담당부서 ", "관리표시 ")
    )
    page = extract_page(WELFARE, FIRST, html.encode())
    assert page.body_contacts == ["가상복지국 > 가상업무과 > 가상업무팀"]
    assert page.footer_department is None and "footer_department_missing" in page.review_issues


def test_general_sentence_starting_with_inquiry_is_not_a_contact_label() -> None:
    html = DETAIL_HTML.decode().replace("문의처</b>:", "문의하시면 안내합니다.</b>")
    page = extract_page(WELFARE, FIRST, html.encode())
    assert not page.body_contacts and "body_contact_unresolved" in page.review_issues


@pytest.mark.parametrize("kind", ["saved_html", "rendered_dom"])
def test_local_inputs_cannot_claim_http_fetch_time(kind) -> None:
    with pytest.raises(CollectionError, match="local_input_has_no_fetch_time"):
        extract_page(WELFARE, FIRST, DETAIL_HTML, input_kind=kind, fetched_at=datetime.now(UTC))


def test_organization_uses_header_name_and_retains_repeated_duty_rows() -> None:
    page = extract_page(ORG, ORG.seed_url, ORG_HTML, synthetic=True)
    assert page.page_kind == "organization" and page.records_seen == page.records_extracted == 3
    assert [row.unit_name for row in page.work_rows] == ["가상도로과", "가상도로과", "가상조명팀"]
    assert [row.row_number for row in page.work_rows] == [1, 2, 3]
    assert page.work_rows[0].duty == page.work_rows[1].duty
    assert "repeated_duty_text" in page.review_issues
    assert "jurisdiction_mapping_requires_review" in page.review_issues
    output = page.model_dump_json()
    for excluded in ("가상담당자", "가상주무관", "031-000", "tel:"):
        assert excluded not in output


@pytest.mark.parametrize(
    "old,new",
    [
        ("담당업무", "변경된 항목"),
        ("<h4>", "<h5>"),
        ("content-table", "different-table"),
    ],
)
def test_organization_does_not_guess_changed_headers_or_selectors(old: str, new: str) -> None:
    with pytest.raises(CollectionError):
        extract_page(ORG, ORG.seed_url, ORG_HTML.decode().replace(old, new).encode())


def test_organization_rowspans_do_not_shift_staff_data_into_duties() -> None:
    html = ORG_HTML.replace(b'<td class="is-td-left">', b'<td class="is-td-left" rowspan="2">', 1)
    page = extract_page(ORG, ORG.seed_url, html)
    assert page.records_seen == 3 and page.records_extracted == 2
    assert not page.extraction_complete and "organization_row_shape_changed" in page.review_issues
    assert "가상담당자" not in page.model_dump_json()


def test_reconciliation_requires_listing_total_and_every_detail() -> None:
    pages = complete_pages()
    complete = report(pages)
    assert complete["completed"] and complete["reconciliation"]["inventory_complete"]
    assert complete["review_status"] == "pending"
    assert complete["reconciliation"]["issue_counts"]["department_labels_differ"] > 0
    partial = report(pages[:2])
    assert not partial["completed"]
    assert partial["reconciliation"]["missing_detail_ids"] == ["9900000002"]
    assert not report(pages, remaining=1)["completed"]
    assert not report(pages[1:])["completed"]


def test_missing_undiscovered_cards_cannot_be_hidden_by_an_empty_queue() -> None:
    pages = complete_pages()
    pages[0].reported_total = 303
    result = report(pages)
    assert result["reconciliation"]["undiscovered_item_count"] == 301
    assert not result["completed"]


def test_changed_total_and_conflicting_list_items_are_visible() -> None:
    pages = complete_pages()
    another = pages[0].model_copy(deep=True)
    another.source_url += "?curPage=2"
    another.reported_total = 3
    another.listing_items[0].title = "다른 합성 제목"
    result = report([*pages, another])
    reconciliation = result["reconciliation"]
    assert reconciliation["reported_total_changed"] and reconciliation["expected_total"] is None
    assert reconciliation["duplicate_listing_ids"] == ["9900000001", "9900000002"]
    assert reconciliation["conflicting_listing_ids"] == ["9900000001"]
    assert not result["completed"]


def test_list_detail_label_mismatch_blocks_inventory_confirmation() -> None:
    pages = complete_pages()
    pages[0].listing_items[0].title = "서로 다른 합성 제목"
    pages[0].listing_items[0].displayed_department = "다른 가상 부서"
    result = report(pages)
    assert {item["code"] for item in result["reconciliation"]["list_detail_mismatches"]} == {
        "listing_detail_title_differs",
        "listing_footer_department_differs",
    }
    assert not result["completed"]


def test_url_duplicates_are_deduplicated_but_changed_inputs_block_completion() -> None:
    pages = complete_pages()
    same = extract_page(WELFARE, FIRST + "?curPage=2", DETAIL_HTML, synthetic=True)
    result = report([*pages, same])
    assert len(result["documents"]) == 2 and result["completed"]
    assert result["reconciliation"]["duplicate_page_urls"] == [FIRST]
    assert len(result["pages"]) == 4
    changed = extract_page(WELFARE, FIRST, DETAIL_HTML + b"<!-- changed input -->", synthetic=True)
    result = report([*pages, changed])
    assert result["reconciliation"]["conflicting_page_urls"] == [FIRST]
    assert not result["completed"]


def test_same_body_at_different_urls_preserves_both_sources_for_review() -> None:
    first = extract_page(WELFARE, FIRST, DETAIL_HTML)
    second = extract_page(WELFARE, SECOND, DETAIL_HTML)
    result = report([first, second])
    assert len(result["documents"]) == 2
    assert result["reconciliation"]["same_body_different_urls"] == [[FIRST, SECOND]]


def test_changed_body_at_one_url_keeps_both_versions_for_review() -> None:
    first = extract_page(WELFARE, FIRST, DETAIL_HTML)
    changed_html = DETAIL_HTML.decode().replace("합성 신청 절차", "변경된 합성 절차").encode()
    second = extract_page(WELFARE, FIRST, changed_html)
    result = report([first, second])
    assert len(result["documents"]) == 2
    assert result["pages"][0]["document_id"] != result["pages"][1]["document_id"]
    assert not result["completed"]


def test_organization_coverage_is_limited_to_registered_codes() -> None:
    pages = [extract_page(ORG, ORG.seed_url, ORG_HTML)]
    result = build_report(ORG, pages, [], mode="local_html", visited=1, remaining_links=0)
    assert len(result["reconciliation"]["missing_organization_codes"]) == 6
    assert not result["completed"]
    pages = [
        extract_page(ORG, ORG.seed_url.replace("38100720000", code), ORG_HTML)
        for code in sorted(ORGANIZATION_CODES)
    ]
    result = build_report(ORG, pages, [], mode="local_manifest", visited=7, remaining_links=0)
    assert result["completed"]
    assert result["reconciliation"]["inventory_scope"] == "registered_organization_codes"


def test_offline_manifest_reports_missing_details_and_preserves_provenance() -> None:
    result = collect_local_manifest(
        WELFARE, FIXTURES / "seongnam_collection_manifest_synthetic.json"
    )
    assert result["mode"] == "local_manifest" and result["remaining_links"] == 2
    assert result["reconciliation"]["missing_detail_ids"] == ["9900000002"]
    assert not result["completed"] and not result["errors"]
    assert result["documents"][0]["synthetic"] and result["documents"][0]["source_url"] is None
    assert result["documents"][0]["fetched_at"] is None


@pytest.mark.parametrize("invalid_path", ["../outside.html", "/outside.html", "missing.html"])
def test_manifest_path_errors_are_reported_without_reading_outside_root(
    tmp_path: Path, invalid_path: str
) -> None:
    path = manifest_file(tmp_path, [{"source_url": FIRST, "input_html": invalid_path}])
    result = collect_local_manifest(WELFARE, path)
    assert not result["completed"] and not result["documents"]
    assert len(result["errors"]) == 1


def test_manifest_rejects_unregistered_urls_without_echoing_private_queries(tmp_path: Path) -> None:
    path = manifest_file(
        tmp_path, [{"source_url": FIRST + "?token=do-not-log", "input_html": "missing.html"}]
    )
    result = collect_local_manifest(WELFARE, path)
    assert "do-not-log" not in json.dumps(result)
    assert result["errors"][0]["url"] == "[unregistered_url]"


def test_anchored_paths_are_rejected_before_filesystem_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = Path(tmp_path.anchor) / "must-not-access.html"
    path = manifest_file(tmp_path, [{"source_url": FIRST, "input_html": str(outside)}])
    original_resolve = Path.resolve

    def checked_resolve(candidate: Path, *args, **kwargs) -> Path:
        assert candidate != outside, "Rejected input must not trigger filesystem resolution"
        return original_resolve(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", checked_resolve)
    result = collect_local_manifest(WELFARE, path)
    assert result["errors"][0]["code"] == "input_path_outside_manifest"


def test_oversized_manifest_is_rejected_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(b" " * 200_001)
    with pytest.raises(CollectionError, match="manifest_too_large"):
        collect_local_manifest(WELFARE, path)


@pytest.mark.parametrize(
    "extra",
    [
        {"schema_version": "99"},
        {"unexpected": "value"},
        {"input_kind": "http"},
        {"source_id": ORG.id},
        {"pages": []},
        {"pages": [{"source_url": FIRST, "input_html": "page.html"}] * 501},
    ],
)
def test_manifest_contract_and_bounds_fail_closed(tmp_path: Path, extra: dict) -> None:
    path = manifest_file(tmp_path, [{"source_url": FIRST, "input_html": "page.html"}], **extra)
    with pytest.raises(CollectionError, match="manifest_"):
        collect_local_manifest(WELFARE, path)


def test_manifest_continues_valid_inputs_after_a_missing_file(tmp_path: Path) -> None:
    (tmp_path / "detail.html").write_bytes(DETAIL_HTML)
    path = manifest_file(
        tmp_path,
        [
            {"source_url": SECOND, "input_html": "missing.html"},
            {"source_url": FIRST, "input_html": "detail.html"},
        ],
        input_kind="rendered_dom",
    )
    result = collect_local_manifest(WELFARE, path)
    assert len(result["documents"]) == len(result["errors"]) == 1
    assert result["pages"][0]["input_kind"] == "rendered_dom"
    assert not result["completed"]


class StructuredFetcher:
    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def get(self, url: str, *, max_bytes: int, timeout: float) -> tuple[bytes, str]:
        self.urls.append(url)
        if url.endswith("robots.txt"):
            return b"User-agent: *\nAllow: /\n", "text/plain"
        return self.pages[url], "text/html"


@pytest.mark.parametrize("source", [WELFARE, ORG])
def test_new_sources_stay_disabled_without_any_network_call(source) -> None:
    fetcher = StructuredFetcher({})
    with pytest.raises(CollectionError, match="review_required"):
        collect(source, fetcher)
    assert fetcher.urls == []


def test_network_collection_keeps_listing_evidence_and_respects_page_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collection.time, "sleep", lambda _: None)
    fetcher = StructuredFetcher({WELFARE.seed_url: LIST_HTML, FIRST: DETAIL_HTML})
    result = collect(replace(WELFARE, collection_reviewed=True), fetcher, max_pages=2)
    assert len(fetcher.urls) == 3 and len(result["pages"]) == 2
    assert len(result["documents"]) == 1 and result["documents"][0]["fetched_at"] is not None
    assert result["pages"][0]["input_kind"] == "http"
    assert result["reconciliation"]["missing_detail_ids"] == ["9900000002"]
    assert not result["completed"]


def test_network_layout_failure_is_an_error_not_a_completed_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collection.time, "sleep", lambda _: None)
    fetcher = StructuredFetcher({WELFARE.seed_url: b"<html>maintenance</html>"})
    result = collect(replace(WELFARE, collection_reviewed=True), fetcher)
    assert result["errors"][0]["code"] == "page_structure_not_verified"
    assert not result["completed"]


def test_cli_writes_partial_report_with_visible_status_and_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "collect_services",
            "--source",
            WELFARE.id,
            "--input-manifest",
            str(FIXTURES / "seongnam_collection_manifest_synthetic.json"),
            "--output",
            str(output),
        ],
    )
    assert main() == 0
    status = json.loads(capsys.readouterr().out)
    assert status["output_written"] and not status["completed"]
    before = output.read_bytes()
    assert json.loads(before)["schema_version"] == "2"
    assert main() == 1 and output.read_bytes() == before


def test_cli_manifest_metadata_cannot_be_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "collect_services",
            "--source",
            WELFARE.id,
            "--input-manifest",
            str(FIXTURES / "seongnam_collection_manifest_synthetic.json"),
            "--synthetic",
            "--output",
            str(output),
        ],
    )
    assert main() == 1 and not output.exists()
