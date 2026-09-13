"""Validate a saved report and recompute a read-only review view; never fetch or publish."""

import hashlib
import json
import re
from collections import Counter
from urllib.parse import parse_qs, urlsplit

from app.collection_review_schemas import CollectionReportInput, ReportPage
from app.collection_schemas import ExtractedPage
from app.services.collection_report import build_report, normalized
from app.services.collection_sources import SOURCES, CollectionError, CollectionSource
from app.services.pii import redact_pii

MAX_REPORT_BYTES = 8_000_000
SOURCE_LABELS = {
    "seongnam-welfare": "성남시 복지사업",
    "seongnam-organization": "성남시 조직도 업무분장",
    "seongnam-handbook": "성남시 민원편람",
    "seongnam-services": "성남시 민원·제안·신고",
}
ISSUES = {
    "usage_review_required": (
        "source",
        "출처 이용 조건 확인",
        "검색과 학습의 이용 조건을 각각 확인해 주세요.",
    ),
    "department_labels_differ": (
        "department",
        "본문과 하단 부서 표기가 다름",
        "두 표기가 어떤 역할인지 원문과 조직도에서 대조해 주세요.",
    ),
    "body_contact_unresolved": (
        "department",
        "본문 문의처 확인 필요",
        "본문에서 문의처를 추출하지 못했습니다.",
    ),
    "footer_department_missing": (
        "department",
        "하단 부서 표시 확인 필요",
        "하단에 표시된 부서를 확인하지 못했습니다.",
    ),
    "jurisdiction_mapping_requires_review": (
        "department",
        "팀별 관할 확인 필요",
        "팀명과 업무 문구만으로 실제 배정 대상을 확정하지 않습니다.",
    ),
    "policy_years_require_review": (
        "source",
        "본문의 정책 연도 확인",
        "등장한 연도와 실제 적용 기간을 구분해 주세요.",
    ),
    "repeated_duty_text": (
        "duplicate",
        "같은 업무 문구가 반복됨",
        "업무 행은 보존했습니다. 같은 문구가 같은 사람이나 업무라는 뜻은 아닙니다.",
    ),
    "organization_row_shape_changed": (
        "missing",
        "업무분장 표 구조 변경",
        "읽지 못한 행이 있습니다. 원문의 열과 셀 병합을 확인해 주세요.",
    ),
    "organization_duty_missing": (
        "missing",
        "담당 업무가 비어 있음",
        "업무가 비어 있는 행을 원문에서 확인해 주세요.",
    ),
    "listing_detail_link_unverified": (
        "missing",
        "상세 이동 확인 필요",
        "카드의 상세 링크를 해석하지 못했습니다.",
    ),
    "listing_text_missing": (
        "missing",
        "목록 제목 또는 요약 누락",
        "카드에 필요한 문구가 없거나 읽히지 않았습니다.",
    ),
    "listing_total_smaller_than_page": (
        "missing",
        "표시 총수와 카드 수가 맞지 않음",
        "목록 표시 총수와 실제 카드를 다시 대조해 주세요.",
    ),
    "page_structure_not_verified": (
        "missing",
        "페이지 구조 확인 필요",
        "이 수집기가 알고 있는 본문 구조와 다릅니다.",
    ),
}


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise CollectionError("duplicate_json_key")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise CollectionError("invalid_json_constant")


def canonical(source: CollectionSource, url: str) -> str:
    result = source.canonical_url(url)
    if result is None:
        raise CollectionError("report_url_not_allowed")
    return result


def restore_page(source: CollectionSource, evidence: ReportPage, documents: dict) -> ExtractedPage:
    url = canonical(source, evidence.source_url)
    expected_kind = (
        "organization"
        if source.profile == "organization"
        else ("welfare_detail" if urlsplit(url).path.count("/") == 2 else "welfare_list")
        if source.profile == "welfare"
        else "legacy"
    )
    if evidence.source_id != source.id or evidence.page_kind != expected_kind:
        raise CollectionError("report_source_kind_mismatch")
    if (
        not evidence.input_sha256
        or len(evidence.input_sha256) != 64
        or any(char not in "0123456789abcdef" for char in evidence.input_sha256)
    ):
        raise CollectionError("invalid_input_hash")
    if evidence.processed_at.tzinfo is None:
        raise CollectionError("report_timezone_required")
    if evidence.page_kind != "welfare_list" and (
        evidence.listing_items or evidence.welfare_filters or evidence.reported_total is not None
    ):
        raise CollectionError("report_fields_do_not_match_page_kind")
    if evidence.page_kind != "organization" and evidence.work_rows:
        raise CollectionError("report_fields_do_not_match_page_kind")
    if evidence.page_kind != "welfare_detail" and (
        evidence.body_contacts or evidence.footer_department or evidence.policy_year_mentions
    ):
        raise CollectionError("report_fields_do_not_match_page_kind")
    doc = documents.get(evidence.document_id)
    if evidence.page_kind == "welfare_list":
        if evidence.document_id is not None or evidence.reported_total is None:
            raise CollectionError("invalid_listing_evidence")
        extracted = len(evidence.listing_items)
    else:
        if doc is None or doc.source_id != source.id or doc.synthetic != evidence.synthetic:
            raise CollectionError("report_document_reference_invalid")
        if doc.source_url is not None and canonical(source, doc.source_url) != url:
            raise CollectionError("report_document_url_mismatch")
        if evidence.synthetic and doc.source_url is not None:
            raise CollectionError("synthetic_source_has_url")
        if evidence.input_kind != "http" and doc.fetched_at is not None:
            raise CollectionError("local_input_has_no_fetch_time")
        if evidence.input_kind == "http" and doc.fetched_at is None:
            raise CollectionError("http_fetch_time_missing")
        if doc.retrieval_use != "unknown" or doc.training_use != "unknown":
            raise CollectionError("extraction_report_is_not_usage_approval")
        extracted = len(evidence.work_rows) if evidence.page_kind == "organization" else 1
    if evidence.page_kind == "welfare_detail" and evidence.source_code != url.rsplit("/", 1)[1]:
        raise CollectionError("report_detail_id_mismatch")
    if (
        evidence.page_kind == "organization"
        and evidence.source_code != parse_qs(urlsplit(url).query)["deptCode"][0]
    ):
        raise CollectionError("report_organization_code_mismatch")
    if evidence.records_extracted != extracted or not extracted <= evidence.records_seen <= 20_000:
        raise CollectionError("report_record_count_invalid")
    if evidence.extraction_complete and evidence.records_seen != extracted:
        raise CollectionError("report_incomplete_rows_hidden")
    for item in evidence.listing_items:
        item_url = canonical(source, item.source_url)
        if (
            source.profile != "welfare"
            or urlsplit(item_url).path.count("/") != 2
            or item.source_code != urlsplit(item_url).path.rsplit("/", 1)[1]
        ):
            raise CollectionError("report_listing_id_mismatch")
        item.source_url = item_url
    page = ExtractedPage.model_validate(
        {
            **evidence.model_dump(exclude={"document_id", "document"}),
            "source_url": url,
            "document": doc,
            "discovered_links": sorted(
                {canonical(source, link) for link in evidence.discovered_links}
            ),
        }
    )
    # Recreate the conditions we can check from the evidence, even if issue labels were removed.
    issues = set(page.review_issues) | {"usage_review_required"}
    if page.page_kind == "welfare_detail":
        assert page.document is not None
        page.policy_year_mentions = sorted(
            {int(year) for year in re.findall(r"(?<!\d)((?:19|20)\d{2})\s*년", page.document.text)}
        )
        if not page.body_contacts:
            issues.add("body_contact_unresolved")
        if not page.footer_department:
            issues.add("footer_department_missing")
        if page.footer_department and any(
            normalized(contact) != normalized(page.footer_department)
            for contact in page.body_contacts
        ):
            issues.add("department_labels_differ")
        if page.policy_year_mentions:
            issues.add("policy_years_require_review")
    if page.page_kind == "organization":
        issues.add("jurisdiction_mapping_requires_review")
        if any(
            count > 1
            for count in Counter((row.unit_name, row.duty) for row in page.work_rows).values()
        ):
            issues.add("repeated_duty_text")
    page.review_issues = sorted(issues)
    return page


def inspect_report(raw: bytes) -> dict:
    if len(raw) > MAX_REPORT_BYTES:
        raise CollectionError("report_too_large")
    try:
        json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        report = CollectionReportInput.model_validate_json(raw, strict=True)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise CollectionError("report_format_invalid") from None
    source = SOURCES.get(report.source_id)
    if source is None:
        raise CollectionError("report_source_unknown")
    if redact_pii(report.model_dump_json()).detected_types:
        raise CollectionError("report_contains_direct_identifiers")
    documents = {doc.id: doc for doc in report.documents}
    references = {page.document_id for page in report.pages if page.document_id is not None}
    if len(documents) != len(report.documents) or references != set(documents):
        raise CollectionError("report_document_reference_invalid")
    pages = [restore_page(source, page, documents) for page in report.pages]
    if len({page.synthetic for page in pages}) > 1:
        raise CollectionError("mixed_synthetic_and_official_evidence")
    if any((page.input_kind == "http") != (report.mode == "network") for page in pages):
        raise CollectionError("report_mode_mismatch")
    if report.visited != len(pages) + len(report.errors):
        raise CollectionError("report_visit_count_invalid")
    if report.mode == "local_html" and (len(pages) != 1 or report.errors):
        raise CollectionError("report_local_page_count_invalid")
    errors = []
    for item in report.errors:
        value = item.model_dump(exclude_none=True)
        if item.url != "[unregistered_url]":
            value["url"] = canonical(source, item.url)
        errors.append(value)
    if report.discovered_links is not None:
        for link in report.discovered_links:
            canonical(source, link)
    acquired = {page.source_url for page in pages}
    discovered = {link for page in pages for link in page.discovered_links}
    if report.mode == "network":
        discovered.add(source.seed_url)
        acquired.update(item["url"] for item in errors)
    remaining = len(discovered - acquired)
    recomputed = build_report(
        source, pages, errors, mode=report.mode, visited=report.visited, remaining_links=remaining
    )
    matches = (
        recomputed["reconciliation"] == report.reconciliation.model_dump(mode="json")
        and recomputed["completed"] == report.completed
        and report.remaining_links == remaining
    )
    return review_view(source, pages, recomputed, hashlib.sha256(raw).hexdigest(), matches)


def review_view(
    source: CollectionSource, pages: list[ExtractedPage], report: dict, digest: str, matches: bool
) -> dict:
    synthetic = bool(pages) and all(page.synthetic for page in pages)
    tasks: list[dict] = []
    evidence = []

    def add(
        kind: str,
        title: str,
        reason: str,
        *,
        page_index: int | None = None,
        source_code: str | None = None,
        source_url: str | None = None,
    ) -> None:
        tasks.append(
            {
                "id": f"R{len(tasks) + 1:04}",
                "kind": kind,
                "title": title,
                "reason": reason,
                "page_index": page_index,
                "source_code": source_code,
                "source_url": None if synthetic else source_url,
                "status": "pending",
            }
        )

    for index, page in enumerate(pages):
        title = page.document.title if page.document else "복지사업 목록"
        evidence.append(
            {
                "index": index,
                "title": title,
                "page_kind": page.page_kind,
                "source_code": page.source_code,
                "source_url": None if page.synthetic else page.source_url,
                "input_kind": page.input_kind,
                "processed_at": page.processed_at.isoformat(),
                "input_sha256": page.input_sha256,
                "records_seen": page.records_seen,
                "records_extracted": page.records_extracted,
                "body_contacts": page.body_contacts,
                "footer_department": page.footer_department,
                "policy_year_mentions": page.policy_year_mentions,
                "body": page.document.text if page.document else None,
                "listing_items": [
                    item.model_dump(exclude={"source_url"}) for item in page.listing_items
                ],
                "work_rows": [row.model_dump() for row in page.work_rows],
            }
        )
        for issue in page.review_issues:
            kind, label, reason = ISSUES.get(
                issue, ("source", "추출 근거 확인", f"수집기 확인 코드: {issue}")
            )
            add(kind, f"{title} · {label}", reason, page_index=index, source_code=page.source_code)
        if not page.extraction_complete:
            add(
                "missing",
                f"{title} · 일부 항목 추출 실패",
                "성공한 항목만 표시되어 있습니다. 원문에서 누락을 확인해 주세요.",
                page_index=index,
            )

    reconciliation = report["reconciliation"]
    listing = {item.source_code: item for page in pages for item in page.listing_items}
    for code in reconciliation["missing_detail_ids"]:
        item = listing[code]
        add(
            "missing",
            f"{item.title} · 상세 페이지 누락",
            "목록은 있지만 상세 본문이 없습니다.",
            source_code=code,
            source_url=item.source_url,
            page_index=next(
                index
                for index, page in enumerate(pages)
                if any(item.source_code == code for item in page.listing_items)
            ),
        )
    for code in reconciliation["unlisted_detail_ids"]:
        add(
            "missing",
            "목록에서 찾지 못한 상세 페이지",
            "목록에 이 번호의 항목이 있는지 확인해 주세요.",
            source_code=code,
        )
    for code in reconciliation["missing_organization_codes"]:
        add(
            "missing",
            "조직도 페이지 누락",
            "등록된 수집 범위의 조직도 파일이 없습니다.",
            source_code=code,
        )
    if reconciliation["undiscovered_item_count"]:
        add(
            "missing",
            f"목록 항목 {reconciliation['undiscovered_item_count']}개 더 필요",
            "표시 총건수보다 확보한 고유 목록 항목이 적습니다.",
        )
    if reconciliation["reported_total_changed"]:
        add(
            "duplicate",
            "페이지마다 표시 총건수가 다름",
            "조회 시점이나 목록 범위가 달라졌는지 확인해 주세요.",
        )
    for field, label in (
        ("duplicate_listing_ids", "중복된 목록 항목"),
        ("conflicting_listing_ids", "같은 항목의 내용이 다름"),
    ):
        for code in reconciliation[field]:
            add(
                "duplicate",
                label,
                "여러 페이지에 있는 같은 번호의 항목을 대조해 주세요.",
                source_code=code,
            )
    for field, label in (
        ("duplicate_page_urls", "같은 페이지가 중복 입력됨"),
        ("conflicting_page_urls", "같은 페이지의 입력 내용이 변경됨"),
    ):
        for url in reconciliation[field]:
            indices = [i for i, page in enumerate(pages) if page.source_url == url]
            add(
                "duplicate",
                label,
                "입력 해시와 원문을 대조해 주세요. 변경된 본문도 보존되어 있습니다.",
                page_index=indices[0] if indices else None,
            )
    for urls in reconciliation["same_body_different_urls"]:
        add(
            "duplicate",
            "다른 페이지의 본문이 같음",
            f"{len(urls)}개 출처의 본문 해시가 같습니다. 같은 업무인지 별도로 확인해 주세요.",
            page_index=next(i for i, page in enumerate(pages) if page.source_url == urls[0]),
        )
    for mismatch in reconciliation["list_detail_mismatches"]:
        code = mismatch["source_code"]
        add(
            "department"
            if mismatch["code"] == "listing_footer_department_differs"
            else "duplicate",
            "목록과 상세의 표기가 다름",
            "목록의 제목·부서와 상세 페이지를 대조해 주세요.",
            source_code=code,
            page_index=next(i for i, page in enumerate(pages) if page.source_code == code),
        )
    for item in report["errors"]:
        add(
            "missing",
            "파일 또는 페이지를 읽지 못함",
            f"확인 코드: {item['code']}",
            source_url=item["url"] if item["url"] != "[unregistered_url]" else None,
        )
    if report["remaining_links"]:
        add(
            "missing",
            f"확보하지 못한 연결 페이지 {report['remaining_links']}개",
            "목록·상세의 연결 페이지를 확인해 주세요. 다른 누락 항목과 겹칠 수 있습니다.",
        )
    order = {"missing": 0, "department": 1, "duplicate": 2, "source": 3}
    tasks.sort(key=lambda task: order[task["kind"]])
    return {
        "schema_version": "1",
        "report_sha256": digest,
        "source_id": source.id,
        "source_label": SOURCE_LABELS[source.id],
        "synthetic": synthetic,
        "mode": report["mode"],
        "summary_matches": matches,
        "completed": report["completed"],
        "review_status": "pending",
        "persisted": False,
        "documents": len(report["documents"]),
        "summary": reconciliation,
        "pages": evidence,
        "tasks": tasks,
    }
