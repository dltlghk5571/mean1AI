"""Reviewed public-service data, separate from operational complaint routing."""

import hashlib
from datetime import date, datetime
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OFFICIAL_HOSTS = {"www.seongnam.go.kr", "www.bokjiro.go.kr", "www.data.go.kr"}


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def official_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in OFFICIAL_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.fragment
        or "\\" in value
    ):
        raise ValueError("unsupported_official_url")
    return value


class SourceDocument(DataModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    source_id: str = Field(min_length=1, max_length=80)
    source_url: str | None = None
    title: str = Field(min_length=2, max_length=200)
    text: str = Field(min_length=5, max_length=40_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    fetched_at: datetime | None = None
    ingested_at: datetime
    published_at: date | None = None
    updated_at: date | None = None
    license_label: str = Field(min_length=1, max_length=200)
    retrieval_use: Literal["unknown", "allowed", "blocked"] = "unknown"
    training_use: Literal["unknown", "allowed", "blocked"] = "unknown"
    synthetic: bool = False

    @field_validator("source_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return official_url(value) if value else None

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.content_hash != hashlib.sha256(self.text.encode()).hexdigest():
            raise ValueError("source_content_hash_mismatch")
        if not self.synthetic and not self.source_url:
            raise ValueError("official_source_url_required")
        if (self.fetched_at and self.fetched_at.tzinfo is None) or self.ingested_at.tzinfo is None:
            raise ValueError("fetched_at_timezone_required")
        return self


class ReferencedRecord(DataModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    source_document_id: str = Field(min_length=1, max_length=100)
    source_code: str | None = Field(default=None, max_length=100)


class TaxonomyNode(ReferencedRecord):
    label: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None


class OrganizationUnit(ReferencedRecord):
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None
    jurisdiction: str = Field(min_length=1, max_length=100)


class WorkAssignment(ReferencedRecord):
    organization_id: str
    description: str = Field(min_length=2, max_length=500)
    jurisdiction: str = Field(min_length=1, max_length=100)


class RequiredInformation(DataModel):
    field_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    question: str = Field(min_length=5, max_length=200)
    required: bool = True


class ServiceCard(DataModel):
    service_id: str = Field(max_length=100)
    title: str = Field(max_length=200)
    summary: str = Field(max_length=1500)
    source_url: str | None
    source_title: str = Field(max_length=200)
    catalog_version: str = Field(max_length=80)
    review_due_at: str
    synthetic: bool
    requires_human_review: bool = True

    @field_validator("source_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return official_url(value) if value else None


class PublicService(ReferencedRecord):
    title: str = Field(min_length=2, max_length=200)
    summary: str = Field(min_length=5, max_length=1500)
    taxonomy_ids: list[str] = Field(default_factory=list, max_length=20)
    work_assignment_ids: list[str] = Field(default_factory=list, max_length=20)
    regions: list[Literal["KR", "GYEONGGI", "SEONGNAM"]] = Field(min_length=1, max_length=3)
    required_information: list[RequiredInformation] = Field(default_factory=list, max_length=20)
    effective_from: date | None = None
    effective_until: date | None = None
    requires_human_review: bool = True

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if (
            self.effective_from
            and self.effective_until
            and self.effective_from > self.effective_until
        ):
            raise ValueError("invalid_effective_period")
        keys = [item.field_id for item in self.required_information]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_required_field")
        return self


class ServiceBundle(DataModel):
    schema_version: Literal["1"] = "1"
    version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    documents: list[SourceDocument] = Field(min_length=1, max_length=200)
    taxonomy: list[TaxonomyNode] = Field(default_factory=list, max_length=500)
    organizations: list[OrganizationUnit] = Field(default_factory=list, max_length=500)
    work_assignments: list[WorkAssignment] = Field(default_factory=list, max_length=500)
    services: list[PublicService] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        groups = (
            self.documents,
            self.taxonomy,
            self.organizations,
            self.work_assignments,
            self.services,
        )
        for group in groups:
            ids = [item.id for item in group]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate_record_id")
        doc_ids = {item.id for item in self.documents}
        for group in groups[1:]:
            for item in group:
                if item.source_document_id not in doc_ids:
                    raise ValueError("unknown_source_document")
        for hierarchy in (self.taxonomy, self.organizations):
            parents = {item.id: item.parent_id for item in hierarchy}
            for start in parents:
                visited: set[str] = set()
                current: str | None = start
                while current is not None:
                    if current in visited or current not in parents:
                        raise ValueError("invalid_hierarchy")
                    visited.add(current)
                    current = parents[current]
        org_ids = {item.id for item in self.organizations}
        if any(item.organization_id not in org_ids for item in self.work_assignments):
            raise ValueError("unknown_organization")
        category_ids = {item.id for item in self.taxonomy}
        work_ids = {item.id for item in self.work_assignments}
        for service in self.services:
            if (
                not set(service.taxonomy_ids) <= category_ids
                or not set(service.work_assignment_ids) <= work_ids
            ):
                raise ValueError("unknown_service_mapping")
        return self
