import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.schemas import ClassificationCandidate, ClassificationResult, Urgency


class ClassifierError(RuntimeError):
    """Raised when a classifier cannot return a safe, valid result."""


class Classifier(Protocol):
    def classify(self, *, title: str, text: str, location_text: str | None) -> ClassificationResult:
        """Classify already-redacted complaint text."""


@dataclass(frozen=True)
class DepartmentInfo:
    id: str
    name: str
    category: str
    description: str


class DepartmentCatalog:
    def __init__(self, departments: list[DepartmentInfo]) -> None:
        self.departments = departments
        self.by_id = {department.id: department for department in departments}

    @classmethod
    def from_json(cls, path: Path) -> "DepartmentCatalog":
        with path.open(encoding="utf-8") as file:
            rows = json.load(file)
        return cls(
            [
                DepartmentInfo(
                    id=str(row["id"]),
                    name=str(row["name"]),
                    category=str(row["category"]),
                    description=str(row["description"]),
                )
                for row in rows
                if bool(row.get("active", True))
            ]
        )

    def as_prompt_data(self) -> list[dict[str, str]]:
        return [
            {
                "id": department.id,
                "name": department.name,
                "category": department.category,
                "description": department.description,
            }
            for department in self.departments
        ]


@dataclass(frozen=True)
class RuleDefinition:
    category: str
    subcategory: str
    department_id: str
    keywords: tuple[str, ...]
    requires_location: bool = True


_RULES: tuple[RuleDefinition, ...] = (
    RuleDefinition(
        category="streetlight",
        subcategory="가로등·보안등 고장",
        department_id="ROAD_LIGHTING",
        keywords=("가로등", "보안등", "조명", "불이 꺼", "불꺼", "깜빡"),
    ),
    RuleDefinition(
        category="road_damage",
        subcategory="도로·보도 파손",
        department_id="ROAD_MAINTENANCE",
        keywords=("포트홀", "도로 파임", "아스팔트", "보도블록", "도로가 패", "도로 파손"),
    ),
    RuleDefinition(
        category="waste",
        subcategory="쓰레기 수거·무단투기",
        department_id="WASTE_MANAGEMENT",
        keywords=("쓰레기", "무단투기", "재활용", "수거", "폐기물", "종량제"),
    ),
    RuleDefinition(
        category="park",
        subcategory="공원·놀이터 시설",
        department_id="PARKS",
        keywords=("공원", "놀이터", "운동기구", "벤치", "산책로", "그네"),
    ),
    RuleDefinition(
        category="traffic",
        subcategory="교통·신호·주정차",
        department_id="TRAFFIC",
        keywords=("신호등", "불법주정차", "주차", "버스", "횡단보도", "교통"),
    ),
    RuleDefinition(
        category="water_sewer",
        subcategory="상하수도·배수",
        department_id="WATER_SEWER",
        keywords=("누수", "하수도", "배수", "수돗물", "맨홀", "오수"),
    ),
    RuleDefinition(
        category="welfare",
        subcategory="복지 상담·자격 검토",
        department_id="WELFARE_REVIEW",
        keywords=("복지", "수급자", "기초생활", "장애", "돌봄", "지원금"),
        requires_location=False,
    ),
)


class RuleBasedClassifier:
    provider_name = "rules"

    def __init__(self, catalog: DepartmentCatalog) -> None:
        self.catalog = catalog

    def classify(self, *, title: str, text: str, location_text: str | None) -> ClassificationResult:
        haystack = " ".join(part for part in (title, text, location_text or "") if part).lower()
        scored: list[tuple[float, RuleDefinition, list[str]]] = []

        for rule in _RULES:
            hits = [keyword for keyword in rule.keywords if keyword.lower() in haystack]
            if not hits:
                continue
            confidence = min(0.98, 0.76 + 0.08 * len(hits))
            scored.append((confidence, rule, hits))

        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return ClassificationResult(
                category="other",
                subcategory="소관 확인 필요",
                urgency=Urgency.NORMAL,
                candidates=[
                    ClassificationCandidate(
                        department_id="CIVIL_COORDINATION",
                        confidence=0.45,
                        reason="명확한 생활민원 키워드를 찾지 못함",
                    )
                ],
                missing_information=[],
                requires_human_review=True,
                evidence_summary="규칙 사전에서 충분한 분류 근거를 찾지 못했습니다.",
                provider=self.provider_name,
            )

        candidates: list[ClassificationCandidate] = []
        seen: set[str] = set()
        for confidence, rule, hits in scored:
            if rule.department_id in seen:
                continue
            if rule.department_id not in self.catalog.by_id:
                continue
            candidates.append(
                ClassificationCandidate(
                    department_id=rule.department_id,
                    confidence=confidence,
                    reason=f"감지 표현: {', '.join(hits[:3])}",
                )
            )
            seen.add(rule.department_id)
            if len(candidates) == 3:
                break

        best_confidence, best_rule, best_hits = scored[0]
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
            evidence_summary=f"규칙 기반 감지 표현: {', '.join(best_hits[:4])}",
            provider=self.provider_name,
        )
