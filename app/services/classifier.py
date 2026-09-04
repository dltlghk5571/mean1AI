from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.schemas import ClassificationCandidate, ClassificationResult, Urgency


class ClassifierError(RuntimeError):
    """Raised when a classifier cannot return a safe, valid result."""


class Classifier(Protocol):
    def classify(self, *, title: str, text: str, location_text: str | None) -> ClassificationResult:
        """Classify already-redacted complaint text."""


class WorkAssignmentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)


class RoutingRuleInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9_-]+$")
    subcategory: str = Field(min_length=1, max_length=120)
    keywords: list[str] = Field(min_length=1, max_length=30)
    requires_location: bool = True
    work_assignment_ids: list[str] = Field(min_length=1, max_length=10)

    @field_validator("keywords", "work_assignment_ids")
    @classmethod
    def reject_blank_or_duplicate_values(cls, values: list[str]) -> list[str]:
        stripped = [value.strip() for value in values]
        if any(not value for value in stripped):
            raise ValueError("catalog lists cannot contain blank values")
        if len(stripped) != len(set(stripped)):
            raise ValueError("catalog lists cannot contain duplicate values")
        return stripped


class DepartmentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9_]+$")
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1_000)
    jurisdiction: str = Field(min_length=1, max_length=120)
    active: bool = True
    work_assignments: list[WorkAssignmentInfo] = Field(min_length=1, max_length=30)
    routing_rules: list[RoutingRuleInfo] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_local_references(self) -> DepartmentInfo:
        assignment_ids = [assignment.id for assignment in self.work_assignments]
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ValueError(f"duplicate work-assignment ID in department {self.id}")
        if not self.active and self.routing_rules:
            raise ValueError(f"inactive department {self.id} cannot have routing rules")
        known_assignments = set(assignment_ids)
        for rule in self.routing_rules:
            unknown = sorted(set(rule.work_assignment_ids) - known_assignments)
            if unknown:
                raise ValueError(
                    f"routing rule {rule.id} references unknown work assignments: {unknown}"
                )
        return self


class DepartmentCatalogDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_version: str = Field(
        min_length=3,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9._-]+$",
    )
    effective_from: date
    effective_until: date | None = None
    approval_status: Literal["approved"]
    source_label: str = Field(min_length=1, max_length=160)
    synthetic: Literal[True]
    fallback_department_id: str = Field(min_length=3, max_length=64)
    departments: list[DepartmentInfo] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_catalog(self) -> DepartmentCatalogDocument:
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective_until cannot be earlier than effective_from")

        department_ids = [department.id for department in self.departments]
        if len(department_ids) != len(set(department_ids)):
            raise ValueError("department IDs must be unique")
        active_ids = {department.id for department in self.departments if department.active}
        if self.fallback_department_id not in active_ids:
            raise ValueError("fallback_department_id must reference an active department")

        assignment_ids = [
            assignment.id
            for department in self.departments
            for assignment in department.work_assignments
        ]
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ValueError("work-assignment IDs must be globally unique")

        rule_ids = [rule.id for department in self.departments for rule in department.routing_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("routing-rule IDs must be globally unique")
        return self


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    category: str
    subcategory: str
    department_id: str
    keywords: tuple[str, ...]
    work_assignment_ids: tuple[str, ...]
    requires_location: bool = True


class DepartmentCatalog:
    def __init__(self, document: DepartmentCatalogDocument, source_sha256: str) -> None:
        self.document = document
        self.catalog_version = document.catalog_version
        self.effective_from = document.effective_from
        self.effective_until = document.effective_until
        self.approval_status = document.approval_status
        self.source_label = document.source_label
        self.synthetic = document.synthetic
        self.source_sha256 = source_sha256
        self.fallback_department_id = document.fallback_department_id
        self.all_departments = tuple(document.departments)
        self.departments = tuple(
            department for department in document.departments if department.active
        )
        self.by_id = {department.id: department for department in self.departments}
        self.routing_rules = tuple(
            RuleDefinition(
                id=rule.id,
                category=department.category,
                subcategory=rule.subcategory,
                department_id=department.id,
                keywords=tuple(rule.keywords),
                work_assignment_ids=tuple(rule.work_assignment_ids),
                requires_location=rule.requires_location,
            )
            for department in self.departments
            for rule in department.routing_rules
        )

    @classmethod
    def from_json(cls, path: Path) -> DepartmentCatalog:
        raw = path.read_bytes()
        try:
            document = DepartmentCatalogDocument.model_validate_json(raw)
        except ValidationError as exc:
            raise ValueError(f"Invalid department catalog: {exc}") from exc

        today = date.today()
        if document.effective_from > today:
            raise ValueError("Department catalog is not effective yet")
        if document.effective_until is not None and document.effective_until < today:
            raise ValueError("Department catalog has expired")
        return cls(document=document, source_sha256=hashlib.sha256(raw).hexdigest())

    def work_assignment_ids_for(
        self,
        department_id: str,
        *,
        subcategory: str | None = None,
    ) -> tuple[str, ...]:
        department = self.by_id.get(department_id)
        if department is None:
            return ()
        if subcategory is not None:
            matched = {
                assignment_id
                for rule in department.routing_rules
                if rule.subcategory == subcategory
                for assignment_id in rule.work_assignment_ids
            }
            if matched:
                return tuple(sorted(matched))
        return tuple(assignment.id for assignment in department.work_assignments)

    def bind_classification(self, result: ClassificationResult) -> ClassificationResult:
        """Attach immutable catalog provenance and drop unknown assignment references."""

        bound_candidates: list[ClassificationCandidate] = []
        for candidate in result.candidates:
            department = self.by_id.get(candidate.department_id)
            if department is None:
                continue
            assignment_ids = list(
                self.work_assignment_ids_for(
                    candidate.department_id,
                    subcategory=result.subcategory,
                )
            )
            bound_candidates.append(
                candidate.model_copy(
                    update={
                        "catalog_version": self.catalog_version,
                        "work_assignment_ids": assignment_ids,
                    }
                )
            )
        if not bound_candidates:
            raise ClassifierError("Classifier returned no active catalog department ID")
        return result.model_copy(update={"candidates": bound_candidates})

    def as_prompt_data(self) -> list[dict[str, object]]:
        return [
            {
                "id": department.id,
                "name": department.name,
                "category": department.category,
                "description": department.description,
                "work_assignments": [
                    {"id": assignment.id, "title": assignment.title}
                    for assignment in department.work_assignments
                ],
            }
            for department in self.departments
        ]

    def as_api_data(self) -> dict[str, object]:
        return {
            "catalog_version": self.catalog_version,
            "effective_from": self.effective_from,
            "effective_until": self.effective_until,
            "approval_status": self.approval_status,
            "source_label": self.source_label,
            "synthetic": self.synthetic,
            "source_sha256": self.source_sha256,
            "fallback_department_id": self.fallback_department_id,
            "departments": [
                department.model_dump(mode="json") for department in self.all_departments
            ],
        }


class RuleBasedClassifier:
    provider_name = "rules"

    def __init__(self, catalog: DepartmentCatalog) -> None:
        self.catalog = catalog

    def classify(self, *, title: str, text: str, location_text: str | None) -> ClassificationResult:
        haystack = " ".join(part for part in (title, text, location_text or "") if part).lower()
        scored: list[tuple[float, RuleDefinition, list[str]]] = []

        for rule in self.catalog.routing_rules:
            hits = [keyword for keyword in rule.keywords if keyword.lower() in haystack]
            if not hits:
                continue
            confidence = min(0.98, 0.76 + 0.08 * len(hits))
            scored.append((confidence, rule, hits))

        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            fallback_id = self.catalog.fallback_department_id
            return ClassificationResult(
                category=self.catalog.by_id[fallback_id].category,
                subcategory="소관 확인 필요",
                urgency=Urgency.NORMAL,
                candidates=[
                    ClassificationCandidate(
                        department_id=fallback_id,
                        confidence=0.45,
                        reason="명확한 생활민원 키워드를 찾지 못함",
                        catalog_version=self.catalog.catalog_version,
                        work_assignment_ids=list(self.catalog.work_assignment_ids_for(fallback_id)),
                    )
                ],
                missing_information=[],
                requires_human_review=True,
                evidence_summary="업무분장 규칙에서 충분한 분류 근거를 찾지 못했습니다.",
                provider=self.provider_name,
            )

        candidates: list[ClassificationCandidate] = []
        seen: set[str] = set()
        for confidence, rule, hits in scored:
            if rule.department_id in seen:
                continue
            candidates.append(
                ClassificationCandidate(
                    department_id=rule.department_id,
                    confidence=confidence,
                    reason=f"업무 규칙 {rule.id} · 감지 표현: {', '.join(hits[:3])}",
                    catalog_version=self.catalog.catalog_version,
                    work_assignment_ids=list(rule.work_assignment_ids),
                )
            )
            seen.add(rule.department_id)
            if len(candidates) == 3:
                break

        _best_confidence, best_rule, best_hits = scored[0]
        missing_information: list[str] = []
        if best_rule.requires_location and not (location_text and location_text.strip()):
            missing_information.append("정확한 발생 위치 또는 지도 핀")

        return ClassificationResult(
            category=best_rule.category,
            subcategory=best_rule.subcategory,
            urgency=Urgency.NORMAL,
            candidates=candidates,
            missing_information=missing_information,
            requires_human_review=bool(missing_information),
            evidence_summary=f"업무분장 규칙 감지 표현: {', '.join(best_hits[:4])}",
            provider=self.provider_name,
        )
