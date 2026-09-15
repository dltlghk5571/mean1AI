import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import ServiceCatalogReview, ServiceCatalogVersion
from app.services.collection_report import build_report
from app.services.collection_review import MAX_REPORT_BYTES, inspect_report
from app.services.collection_sources import SOURCES, CollectionError
from app.services.service_collection import extract_page

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "app/data/collection_report_demo.json"
WELFARE = SOURCES["seongnam-welfare"]


def example() -> dict:
    return json.loads(DEMO.read_bytes())


def inspect(value: dict) -> dict:
    return inspect_report(json.dumps(value, ensure_ascii=False).encode())


def test_example_recomputes_missing_detail_department_difference_and_usage_tasks() -> None:
    raw = DEMO.read_bytes()
    result = inspect_report(raw)
    assert result["synthetic"] and result["summary_matches"]
    assert not result["persisted"] and not result["completed"]
    assert result["report_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["summary"]["missing_detail_ids"] == ["9900000002"]
    assert {task["kind"] for task in result["tasks"]} == {"missing", "department", "source"}
    assert all(
        task["status"] == "pending" and task["source_url"] is None for task in result["tasks"]
    )
    assert all(page["source_url"] is None for page in result["pages"])
    assert result["pages"][1]["body_contacts"] != [result["pages"][1]["footer_department"]]


def test_tampered_summary_and_removed_issue_labels_are_recomputed() -> None:
    value = example()
    value["completed"] = True
    value["remaining_links"] = 0
    value["reconciliation"]["missing_detail_ids"] = []
    value["reconciliation"]["inventory_complete"] = True
    value["pages"][1]["review_issues"] = []
    value["pages"][1]["policy_year_mentions"] = []
    result = inspect(value)
    assert not result["summary_matches"] and not result["completed"]
    assert result["summary"]["missing_detail_ids"] == ["9900000002"]
    assert any(task["kind"] == "department" for task in result["tasks"])
    assert result["pages"][1]["policy_year_mentions"] == [2025]
    assert result["review_status"] == "pending"


def test_list_root_cannot_masquerade_as_a_detail_item() -> None:
    report = example()
    report["pages"][0]["listing_items"][0]["source_url"] = WELFARE.seed_url
    report["pages"][0]["listing_items"][0]["source_code"] = "wf-pm020101"
    with pytest.raises(CollectionError, match="report_listing_id_mismatch"):
        inspect(report)


def test_detail_cannot_claim_additional_listing_evidence() -> None:
    report = example()
    report["pages"][1]["listing_items"] = report["pages"][0]["listing_items"]
    with pytest.raises(CollectionError, match="report_fields_do_not_match_page_kind"):
        inspect(report)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "1"),
        ("schema_version", 2),
        ("completed", "false"),
        ("visited", True),
        ("review_status", "approved"),
        ("source_id", "unknown"),
        ("mode", "live_verified"),
        ("unexpected", "not_allowed"),
    ],
)
def test_report_contract_rejects_ambiguous_types_and_approval_flags(field: str, value) -> None:
    report = example()
    report[field] = value
    with pytest.raises(CollectionError):
        inspect(report)


@pytest.mark.parametrize(
    "raw",
    [
        b"null",
        b"[]",
        b"\xff",
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b"[" * 2000,
    ],
    ids=["null", "array", "invalid-encoding", "duplicate-key", "nan", "deep-json"],
)
def test_invalid_json_fails_without_returning_input_values(raw: bytes) -> None:
    with pytest.raises(CollectionError, match="report_format_invalid"):
        inspect_report(raw)


def test_oversize_is_rejected_before_json_parsing() -> None:
    with pytest.raises(CollectionError, match="report_too_large"):
        inspect_report(b" " * (MAX_REPORT_BYTES + 1))


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "unreferenced", "wrong-reference", "wrong-hash"]
)
def test_broken_document_references_and_body_hashes_are_rejected(change: str) -> None:
    report = example()
    if change == "missing":
        report["documents"] = []
    elif change == "duplicate":
        report["documents"].append(report["documents"][0])
    elif change == "unreferenced":
        report["pages"][1]["document_id"] = None
    elif change == "wrong-reference":
        report["pages"][1]["document_id"] = "unknown-document"
    else:
        report["documents"][0]["text"] += " 변경한 합성 문장"
    with pytest.raises(CollectionError):
        inspect(report)


@pytest.mark.parametrize("where", ["page", "card", "error", "link"])
def test_external_or_filtered_urls_are_not_rendered_as_source_links(where: str) -> None:
    report = example()
    malicious = "https://example.invalid/private?secret=not-for-output"
    if where == "page":
        report["pages"][0]["source_url"] = malicious
    elif where == "card":
        report["pages"][0]["listing_items"][0]["source_url"] = malicious
    elif where == "error":
        report["errors"] = [{"code": "source_connection_failed", "url": malicious}]
        report["visited"] += 1
    else:
        report["pages"][0]["discovered_links"] = [WELFARE.seed_url + "?srchText=not-for-output"]
    with pytest.raises(CollectionError) as error:
        inspect(report)
    assert "not-for-output" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", "seongnam-organization"),
        ("page_kind", "organization"),
        ("input_kind", "http"),
        ("input_sha256", "invalid"),
        ("records_extracted", 0),
        ("records_seen", 3),
        ("source_code", "wrong-id"),
    ],
)
def test_inconsistent_page_evidence_is_rejected(field: str, value) -> None:
    report = example()
    report["pages"][1][field] = value
    with pytest.raises(CollectionError):
        inspect(report)


def test_local_evidence_cannot_claim_http_fetch_time_or_usage_permission() -> None:
    report = example()
    report["documents"][0]["fetched_at"] = datetime.now(UTC).isoformat()
    with pytest.raises(CollectionError, match="local_input_has_no_fetch_time"):
        inspect(report)
    report["documents"][0]["fetched_at"] = None
    report["documents"][0]["retrieval_use"] = "allowed"
    with pytest.raises(CollectionError, match="not_usage_approval"):
        inspect(report)


def test_direct_identifiers_are_rejected_after_decoding_json_escapes() -> None:
    report = example()
    report["pages"][1]["body_contacts"] = ["합성 연락처 010-1111-2222"]
    raw = (
        json.dumps(report, ensure_ascii=True)
        .encode()
        .replace(b"010-1111-2222", b"\\u003010-1111-2222")
    )
    with pytest.raises(CollectionError, match="report_contains_direct_identifiers"):
        inspect_report(raw)


def test_official_page_urls_remain_available_without_fetching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Report inspection must not fetch source URLs")

    monkeypatch.setattr("app.services.service_collection.HttpPageFetcher.get", forbidden)
    report = example()
    for page in report["pages"]:
        page["synthetic"] = False
    report["documents"][0]["synthetic"] = False
    report["documents"][0]["source_url"] = report["pages"][1]["source_url"]
    result = inspect(report)
    assert not result["synthetic"]
    assert result["pages"][1]["source_url"].startswith(WELFARE.seed_url)
    assert result["review_status"] == "pending"


def test_organization_report_exposes_missing_scope_and_repeated_work_rows() -> None:
    source = SOURCES["seongnam-organization"]
    html = (ROOT / "tests/fixtures/seongnam_organization_synthetic.html").read_bytes()
    page = extract_page(source, source.seed_url, html, synthetic=True)
    report = build_report(source, [page], [], mode="local_html", visited=1, remaining_links=0)
    result = inspect(report)
    assert len(result["summary"]["missing_organization_codes"]) == 6
    assert len(result["pages"][0]["work_rows"]) == 3
    assert any(task["kind"] == "duplicate" for task in result["tasks"])


def test_errors_only_report_has_useful_tasks_without_fabricating_pages() -> None:
    report = build_report(
        WELFARE,
        [],
        [{"code": "local_input_failed", "url": "[unregistered_url]"}],
        mode="local_manifest",
        visited=1,
        remaining_links=0,
    )
    result = inspect(report)
    assert result["pages"] == [] and result["documents"] == 0
    assert len(result["tasks"]) == 1 and not result["completed"]


def test_network_report_counts_a_failed_link_as_attempted() -> None:
    html = (ROOT / "tests/fixtures/seongnam_welfare_list_synthetic.html").read_bytes()
    page = extract_page(
        WELFARE, WELFARE.seed_url, html, input_kind="http", fetched_at=datetime.now(UTC)
    )
    failed = page.discovered_links[0]
    report = build_report(
        WELFARE,
        [page],
        [{"code": "source_connection_failed", "url": failed}],
        mode="network",
        visited=2,
        remaining_links=2,
    )
    result = inspect(report)
    assert result["summary_matches"] and not result["completed"]


def test_completed_extraction_still_has_pending_usage_tasks() -> None:
    source = SOURCES["seongnam-handbook"]
    html = b'<div id="contents"><h1>Synthetic handbook</h1><p>Test guide only.</p></div>'
    page = extract_page(source, source.seed_url, html, synthetic=True)
    report = build_report(source, [page], [], mode="local_html", visited=1, remaining_links=0)
    result = inspect(report)
    assert result["completed"] and result["review_status"] == "pending"
    assert result["tasks"][0]["kind"] == "source"


def test_viewer_and_preview_require_staff_login(anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/staff/source-reports", follow_redirects=False).status_code == 303
    assert anonymous_client.get("/api/v1/source-reports/example").status_code == 401
    assert (
        anonymous_client.post("/api/v1/source-reports/preview", json=example()).status_code == 401
    )


def test_preview_requires_csrf_even_though_it_does_not_persist(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/source-reports/preview", json=example(), headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )


def test_page_preview_and_example_do_not_create_catalog_or_review_records(
    client: TestClient, test_app: FastAPI
) -> None:
    page = client.get("/staff/source-reports")
    assert page.status_code == 200 and "data-report-example" in page.text
    assert page.headers["Cache-Control"] == "no-store"
    assert "data-catalog-import" not in page.text and "data-catalog-review" not in page.text
    preview = client.post(
        "/api/v1/source-reports/preview",
        content=DEMO.read_bytes(),
        headers={"Content-Type": "application/json"},
    )
    assert preview.status_code == 200 and not preview.json()["persisted"]
    assert preview.headers["Cache-Control"] == "no-store"
    assert client.get("/api/v1/source-reports/example").json()["synthetic"]
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(ServiceCatalogVersion.version))) == 0
        assert db.scalar(select(func.count(ServiceCatalogReview.id))) == 0


def test_auditor_can_inspect_without_catalog_import_or_approval(
    anonymous_client: TestClient,
) -> None:
    anonymous_client.post("/login", data={"username": "audit.demo", "password": "audit-demo-2026"})
    csrf = anonymous_client.get("/api/v1/session").json()["csrf_token"]
    response = anonymous_client.post(
        "/api/v1/source-reports/preview", json=example(), headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200 and response.json()["review_status"] == "pending"
    assert (
        anonymous_client.post(
            "/api/v1/service-catalogs", json={}, headers={"X-CSRF-Token": csrf}
        ).status_code
        == 403
    )


def test_api_rejects_wrong_media_type_and_large_or_private_payloads(
    client: TestClient, caplog
) -> None:
    assert (
        client.post(
            "/api/v1/source-reports/preview", content=b"{}", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    response = client.post(
        "/api/v1/source-reports/preview",
        content=b" " * (MAX_REPORT_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    value = example()
    value["pages"][1]["body_contacts"] = ["합성 010-1111-2222"]
    response = client.post("/api/v1/source-reports/preview", json=value)
    assert response.status_code == 422
    assert "010-1111-2222" not in response.text and "010-1111-2222" not in caplog.text
