"""Reconcile extraction evidence. No public catalog approval or database writes."""

from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.collection_schemas import CollectionManifest, ExtractedPage, ListingItem
from app.services.collection_sources import ORGANIZATION_CODES, CollectionError, CollectionSource


def normalized(value: str) -> str:
    return "".join(value.split())


def build_report(
    source: CollectionSource,
    pages: list[ExtractedPage],
    errors: list[dict[str, str]],
    *,
    mode: str,
    visited: int,
    remaining_links: int,
) -> dict:
    by_url: dict[str, ExtractedPage] = {}
    duplicate_urls: list[str] = []
    conflicting_urls: list[str] = []
    for page in pages:
        if page.source_url in by_url:
            duplicate_urls.append(page.source_url)
            if by_url[page.source_url].input_sha256 != page.input_sha256:
                conflicting_urls.append(page.source_url)
        else:
            by_url[page.source_url] = page
    unique = list(by_url.values())
    listing_by_id: dict[str, list[ListingItem]] = defaultdict(list)
    for page in unique:
        for item in page.listing_items:
            listing_by_id[item.source_code].append(item)
    detail_by_id = {
        page.source_code: page
        for page in unique
        if page.page_kind == "welfare_detail" and page.source_code is not None
    }
    reported_totals = sorted(
        {page.reported_total for page in unique if page.reported_total is not None}
    )
    expected_total = reported_totals[0] if len(reported_totals) == 1 else None
    listed_ids = set(listing_by_id)
    detail_ids = set(detail_by_id)
    duplicate_ids = sorted(key for key, values in listing_by_id.items() if len(values) > 1)
    conflicting_ids = sorted(
        key
        for key, values in listing_by_id.items()
        if len(
            {
                (
                    normalized(item.title),
                    normalized(item.summary),
                    normalized(item.displayed_department or ""),
                )
                for item in values
            }
        )
        > 1
    )
    mismatches: list[dict[str, str]] = []
    for code in sorted(listed_ids & detail_ids):
        item = listing_by_id[code][0]
        detail = detail_by_id[code]
        if detail.document and normalized(item.title) != normalized(detail.document.title):
            mismatches.append({"source_code": code, "code": "listing_detail_title_differs"})
        if (
            item.displayed_department
            and detail.footer_department
            and normalized(item.displayed_department) != normalized(detail.footer_department)
        ):
            mismatches.append({"source_code": code, "code": "listing_footer_department_differs"})
    extraction_ok = bool(unique) and not errors and all(p.extraction_complete for p in unique)
    welfare_complete = (
        extraction_ok
        and expected_total is not None
        and len(listed_ids) == expected_total
        and listed_ids == detail_ids
        and not conflicting_ids
        and not mismatches
        and not conflicting_urls
    )
    organization_codes = {
        parse_qs(urlsplit(page.source_url).query)["deptCode"][0]
        for page in unique
        if page.page_kind == "organization"
    }
    inventory_complete = (
        welfare_complete
        if source.profile == "welfare"
        else (
            extraction_ok and organization_codes == ORGANIZATION_CODES and not conflicting_urls
            if source.profile == "organization"
            else None
        )
    )
    bodies: dict[str, list[str]] = defaultdict(list)
    for page in unique:
        if page.document:
            bodies[page.document.content_hash].append(page.source_url)
    documents = {page.document.id: page.document for page in pages if page.document}
    return {
        "schema_version": "2",
        "source_id": source.id,
        "review_status": "pending",
        "mode": mode,
        "documents": [doc.model_dump(mode="json") for doc in documents.values()],
        # Keep conflicting body versions too, linked from each input's review evidence.
        "pages": [
            {
                **page.model_dump(mode="json", exclude={"document"}),
                "document_id": page.document.id if page.document else None,
            }
            for page in pages
        ],
        "errors": errors,
        "visited": visited,
        "remaining_links": remaining_links,
        "completed": bool(
            extraction_ok
            and remaining_links == 0
            and not conflicting_urls
            and (inventory_complete if inventory_complete is not None else True)
        ),
        "reconciliation": {
            "reported_totals": reported_totals,
            "expected_total": expected_total,
            "inventory_scope": {
                "welfare": "unfiltered_welfare_listing",
                "organization": "registered_organization_codes",
                "legacy": "discovered_links_only",
            }[source.profile],
            "reported_total_changed": len(reported_totals) > 1,
            "unique_listing_items": len(listed_ids),
            "collected_detail_items": len(detail_ids),
            "undiscovered_item_count": max(0, expected_total - len(listed_ids))
            if expected_total is not None
            else None,
            "missing_detail_ids": sorted(listed_ids - detail_ids),
            "unlisted_detail_ids": sorted(detail_ids - listed_ids),
            "missing_organization_codes": sorted(ORGANIZATION_CODES - organization_codes)
            if source.profile == "organization"
            else [],
            "duplicate_listing_ids": duplicate_ids,
            "conflicting_listing_ids": conflicting_ids,
            "duplicate_page_urls": sorted(set(duplicate_urls)),
            "conflicting_page_urls": sorted(set(conflicting_urls)),
            "same_body_different_urls": [urls for urls in bodies.values() if len(urls) > 1],
            "list_detail_mismatches": mismatches,
            "issue_counts": dict(Counter(issue for page in pages for issue in page.review_issues)),
            "inventory_complete": inventory_complete,
        },
        "note": (
            "Extraction completeness is not official accuracy, usage permission, "
            "or publication approval."
        ),
    }


def collect_local_manifest(source: CollectionSource, path: Path) -> dict:
    from app.services.service_collection import MAX_BYTES, extract_page

    with path.open("rb") as manifest_stream:
        manifest_bytes = manifest_stream.read(200_001)
    if len(manifest_bytes) > 200_000:
        raise CollectionError("manifest_too_large")
    try:
        manifest = CollectionManifest.model_validate_json(manifest_bytes.decode("utf-8-sig"))
    except ValueError:
        raise CollectionError("manifest_invalid") from None
    if manifest.source_id != source.id:
        raise CollectionError("manifest_source_mismatch")
    directory = path.resolve().parent
    pages: list[ExtractedPage] = []
    errors: list[dict[str, str]] = []
    for index, item in enumerate(manifest.pages, 1):
        canonical = source.canonical_url(item.source_url)
        try:
            if not canonical:
                raise CollectionError("source_url_or_size_not_allowed")
            relative = Path(item.input_html)
            # Reject absolute, root-relative and drive-relative paths before resolving them.
            if relative.anchor or ".." in relative.parts:
                raise CollectionError("input_path_outside_manifest")
            target = (directory / relative).resolve()
            if not target.is_relative_to(directory):
                raise CollectionError("input_path_outside_manifest")
            with target.open("rb") as handle:
                data = handle.read(MAX_BYTES + 1)
            pages.append(
                extract_page(
                    source,
                    canonical,
                    data,
                    synthetic=manifest.synthetic,
                    input_kind=manifest.input_kind,
                )
            )
        except (OSError, ValueError) as exc:
            code = str(exc) if isinstance(exc, CollectionError) else "local_input_failed"
            errors.append(
                {"entry": str(index), "code": code, "url": canonical or "[unregistered_url]"}
            )
    obtained = {page.source_url for page in pages}
    discovered = {url for page in pages for url in page.discovered_links}
    return build_report(
        source,
        pages,
        errors,
        mode="local_manifest",
        visited=len(manifest.pages),
        remaining_links=len(discovered - obtained),
    )
