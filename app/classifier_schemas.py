"""Club classification contract v1; catalog IDs are supplied by the application."""

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import Urgency
from app.services.pii import contains_direct_identifiers

MAX_REQUEST_BYTES = 200_000
MAX_RESPONSE_BYTES = 16_000
ExecutionMode = Literal["synthetic", "model"]
ShortText = Annotated[str, Field(min_length=1, max_length=300)]


class ClassifierModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", hide_input_in_errors=True, str_strip_whitespace=True, allow_inf_nan=False
    )


class ClassifierInput(ClassifierModel):
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=5, max_length=20_000)
    location_text: str = Field(max_length=300)


class ClassifierDepartment(ClassifierModel):
    id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_]{2,63}$")
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=1000)
    jurisdiction: str = Field(max_length=120)
    subcategories: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(max_length=20)


class ClassifierCatalog(ClassifierModel):
    version: str = Field(min_length=3, max_length=80)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    synthetic: Literal[True]
    fallback_department_id: str = Field(max_length=64)
    departments: list[ClassifierDepartment] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_ids(self) -> "ClassifierCatalog":
        ids = [item.id for item in self.departments]
        if len(ids) != len(set(ids)) or self.fallback_department_id not in ids:
            raise ValueError("invalid_classifier_catalog_ids")
        return self


class ClubClassifyRequest(ClassifierModel):
    schema_version: Literal["1"] = "1"
    task: Literal["classify"] = "classify"
    request_id: UUID
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1, max_length=120)
    complaint: ClassifierInput
    catalog: ClassifierCatalog


class ClubCandidate(ClassifierModel):
    department_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_]{2,63}$")
    confidence: float = Field(ge=0, le=1)
    reason: ShortText


class ClassificationProposal(ClassifierModel):
    abstained: bool
    category: str | None = Field(max_length=80)
    subcategory: str | None = Field(max_length=120)
    urgency: Urgency
    candidates: list[ClubCandidate] = Field(max_length=3)
    missing_information: list[ShortText] = Field(max_length=10)
    evidence_summary: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def validate_abstention(self) -> "ClassificationProposal":
        if self.abstained:
            if self.candidates or self.category is not None or self.subcategory is not None:
                raise ValueError("abstention_must_not_recommend_department")
        elif not self.candidates or not self.category or not self.subcategory:
            raise ValueError("classification_requires_candidates_and_category")
        return self


class ClubClassifyResponse(ClassifierModel):
    schema_version: Literal["1"] = "1"
    request_id: UUID
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1, max_length=120)
    catalog_version: str = Field(min_length=3, max_length=80)
    catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_mode: ExecutionMode
    proposal: ClassificationProposal


def input_fingerprint(complaint: ClassifierInput, catalog: ClassifierCatalog) -> str:
    body = {
        "contract": "club-classify-v1",
        "complaint": complaint.model_dump(),
        "catalog": catalog.model_dump(),
    }
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_proposal(request: ClubClassifyRequest, proposal: ClassificationProposal) -> None:
    if contains_direct_identifiers(proposal.model_dump()):
        raise ValueError("classifier_response_contains_identifier")
    if proposal.abstained:
        return
    by_id = {item.id: item for item in request.catalog.departments}
    ids = [item.department_id for item in proposal.candidates]
    if len(ids) != len(set(ids)) or any(identifier not in by_id for identifier in ids):
        raise ValueError("classifier_unknown_or_duplicate_department")
    top = max(proposal.candidates, key=lambda item: item.confidence)
    department = by_id[top.department_id]
    if (
        proposal.category != department.category
        or proposal.subcategory not in department.subcategories
    ):
        raise ValueError("classifier_category_or_subcategory_mismatch")
