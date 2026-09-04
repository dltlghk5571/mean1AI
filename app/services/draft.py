from dataclasses import dataclass

from app.schemas import ClassificationResult
from app.services.classifier import DepartmentCatalog
from app.services.knowledge import KnowledgeDocument


@dataclass(frozen=True)
class DraftResult:
    text: str
    source_ids: list[str]


class GroundedTemplateDrafter:
    def __init__(self, catalog: DepartmentCatalog) -> None:
        self.catalog = catalog

    def generate(
        self,
        *,
        title: str,
        location_text: str | None,
        classification: ClassificationResult,
        documents: list[KnowledgeDocument],
    ) -> DraftResult:
        top = classification.candidates[0]
        department = self.catalog.by_id.get(top.department_id)
        department_name = department.name if department else "민원 조정 데모팀"

        lines = [
            "[답변 초안 — 담당자 검토 및 수정 필요]",
            "",
            f"'{title}' 민원을 접수했습니다.",
            (
                f"분류 제안은 '{classification.subcategory}'이며, "
                f"검토 후보는 '{department_name}'입니다."
            ),
        ]
        if location_text:
            lines.append(f"신고 위치는 '{location_text}'로 입력되었습니다.")
        if classification.missing_information:
            lines.append(
                "정확한 처리를 위해 다음 정보의 추가 확인이 필요합니다: "
                + ", ".join(classification.missing_information)
                + "."
            )
        else:
            review_target = "현장·시설 식별 정보와 실제 소관"
            lines.append(f"담당자가 {review_target}을 확인한 뒤 처리 방향을 안내합니다.")

        source_ids = [document.id for document in documents]
        if documents:
            source_text = ", ".join(
                f"{document.title}({document.version})" for document in documents
            )
            lines.append(f"초안 작성에 참고한 데모 지침: {source_text}.")
        else:
            lines.append("현재 일치하는 승인 데모 지침이 없어 담당자의 직접 검토가 필요합니다.")

        lines.extend(
            [
                "",
                "본 문구는 시연용 시스템의 초안이며 실제 성남시 처리 완료나 "
                "법적 판단을 의미하지 않습니다.",
            ]
        )
        return DraftResult(text="\n".join(lines), source_ids=source_ids)
