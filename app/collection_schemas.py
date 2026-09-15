"""Extraction evidence stays outside the approved ServiceBundle contract."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.service_data_schemas import SourceDocument

InputKind = Literal["saved_html", "rendered_dom", "http"]


class CollectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListingItem(CollectionModel):
    source_code: str
    title: str
    source_url: str
    summary: str
    displayed_department: str | None = None
    tags: list[str] = Field(default_factory=list)


class WelfareFilter(CollectionModel):
    group: str
    source_code: str
    label: str


class WorkRow(CollectionModel):
    unit_name: str
    duty: str
    row_number: int


class ExtractedPage(CollectionModel):
    source_id: str
    source_url: str
    page_kind: Literal["legacy", "welfare_list", "welfare_detail", "organization"]
    extractor_version: Literal["20260909-v2"] = "20260909-v2"
    input_kind: InputKind
    input_sha256: str
    processed_at: datetime
    synthetic: bool
    records_seen: int = 0
    records_extracted: int = 0
    extraction_complete: bool = True
    document: SourceDocument | None = None
    source_code: str | None = None
    discovered_links: list[str] = Field(default_factory=list)
    listing_items: list[ListingItem] = Field(default_factory=list)
    welfare_filters: list[WelfareFilter] = Field(default_factory=list)
    reported_total: int | None = None
    body_contacts: list[str] = Field(default_factory=list)
    footer_department: str | None = None
    policy_year_mentions: list[int] = Field(default_factory=list)
    work_rows: list[WorkRow] = Field(default_factory=list)
    review_issues: list[str] = Field(default_factory=list)


class ManifestPage(CollectionModel):
    source_url: str = Field(min_length=1, max_length=1000)
    input_html: str = Field(min_length=1, max_length=300)


class CollectionManifest(CollectionModel):
    schema_version: Literal["1"]
    source_id: str
    synthetic: bool
    input_kind: Literal["saved_html", "rendered_dom"] = "saved_html"
    pages: list[ManifestPage] = Field(min_length=1, max_length=500)
