"""Read-only evidence viewer input. Catalog publication uses a separate contract."""

from typing import Annotated, Literal

from pydantic import Field

from app.collection_schemas import CollectionModel, ExtractedPage
from app.service_data_schemas import SourceDocument

Count = Annotated[int, Field(ge=0, le=1_000_000)]
Codes = Annotated[list[str], Field(max_length=10_000)]


class ReportPage(ExtractedPage):
    document: None = None
    document_id: str | None = Field(default=None, max_length=100)


class ReportError(CollectionModel):
    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$")
    url: str = Field(min_length=1, max_length=1000)
    entry: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,2}$")


class ListDetailMismatch(CollectionModel):
    source_code: str = Field(min_length=1, max_length=100)
    code: Literal["listing_detail_title_differs", "listing_footer_department_differs"]


class ReportReconciliation(CollectionModel):
    reported_totals: list[Count] = Field(max_length=500)
    expected_total: Count | None
    inventory_scope: Literal[
        "unfiltered_welfare_listing", "registered_organization_codes", "discovered_links_only"
    ]
    reported_total_changed: bool
    unique_listing_items: Count
    collected_detail_items: Count
    undiscovered_item_count: Count | None
    missing_detail_ids: Codes
    unlisted_detail_ids: Codes
    missing_organization_codes: Codes
    duplicate_listing_ids: Codes
    conflicting_listing_ids: Codes
    duplicate_page_urls: Codes
    conflicting_page_urls: Codes
    same_body_different_urls: list[Codes] = Field(max_length=500)
    list_detail_mismatches: list[ListDetailMismatch] = Field(max_length=10_000)
    issue_counts: dict[str, Count]
    inventory_complete: bool | None


class CollectionReportInput(CollectionModel):
    schema_version: Literal["2"]
    source_id: str = Field(min_length=1, max_length=80)
    review_status: Literal["pending"]
    mode: Literal["local_html", "local_manifest", "network"]
    documents: list[SourceDocument] = Field(max_length=500)
    pages: list[ReportPage] = Field(max_length=500)
    errors: list[ReportError] = Field(max_length=500)
    visited: int = Field(ge=0, le=500)
    remaining_links: Count
    completed: bool
    reconciliation: ReportReconciliation
    note: str = Field(max_length=2000)
    discovered_links: Codes | None = None
