import re
from dataclasses import dataclass
from typing import Protocol

from app.schemas import (
    ClassificationResult,
    GroundedDraftSentence,
    RejectedDraftSentence,
    StructuredDraftOutput,
)
from app.services.classifier import DepartmentCatalog
from app.services.knowledge import (
    KnowledgeDocument,
    content_safety_violation,
    tokenize,
)
from app.services.pii import redact_pii

_OPERATIONAL_SENTENCE_PATTERNS = (
    re.compile(r"^\[답변 초안 — 담당자 검토 및 수정 필요\]$"),
    re.compile(r"^'.+' 민원을 접수했습니다\.$", re.DOTALL),
    re.compile(r"^분류 제안은 '.+'이며, 검토 후보는 '.+'입니다\.$", re.DOTALL),
    re.compile(r"^신고 위치는 '.+'로 입력되었습니다\.$", re.DOTALL),
    re.compile(r"^정확한 처리를 위해 다음 정보의 추가 확인이 필요합니다: .+\.$", re.DOTALL),
    re.compile(r"^담당자가 민원 내용과 적용 가능한 지침을 직접 확인합니다\.$"),
    re.compile(r"^일치하는 승인·유효 지침이 없어 담당자의 직접 검토가 필요합니다\.$"),
    re.compile(
        r"^본 문구는 시연용 시스템의 초안이며 실제 성남시 처리 완료나 "
        r"법적 판단을 의미하지 않습니다\.$"
    ),
)


class DraftProvider(Protocol):
    provider_name: str

    def generate(
        self,
        *,
        title: str,
        location_text: str | None,
        classification: ClassificationResult,
        documents: list[KnowledgeDocument],
    ) -> StructuredDraftOutput: ...


@dataclass(frozen=True)
class DraftResult:
    text: str
    provider: str
    validation_status: str
    sentences: list[GroundedDraftSentence]
    rejected_sentences: list[RejectedDraftSentence]
    source_ids: list[str]
    requires_human_review: bool


def _first_sentence(body: str) -> str:
    normalized = re.sub(r"\s+", " ", body).strip()
    sentences = re.split(r"(?<=[.!?])\s+", normalized, maxsplit=1)
    return sentences[0]


class RulesGroundedDraftProvider:
    """Deterministic, offline provider with the same structured boundary as a model."""

    provider_name = "rules"

    def __init__(self, catalog: DepartmentCatalog) -> None:
        self.catalog = catalog

    def generate(
        self,
        *,
        title: str,
        location_text: str | None,
        classification: ClassificationResult,
        documents: list[KnowledgeDocument],
    ) -> StructuredDraftOutput:
        top = classification.candidates[0]
        department = self.catalog.by_id.get(top.department_id)
        department_name = department.name if department else "민원 조정 데모팀"
        sentences = [
            GroundedDraftSentence(
                text="[답변 초안 — 담당자 검토 및 수정 필요]",
                substantive=False,
                source_ids=[],
            ),
            GroundedDraftSentence(
                text=f"'{title}' 민원을 접수했습니다.",
                substantive=False,
                source_ids=[],
            ),
            GroundedDraftSentence(
                text=(
                    f"분류 제안은 '{classification.subcategory}'이며, "
                    f"검토 후보는 '{department_name}'입니다."
                ),
                substantive=False,
                source_ids=[],
            ),
        ]
        if location_text:
            sentences.append(
                GroundedDraftSentence(
                    text=f"신고 위치는 '{location_text}'로 입력되었습니다.",
                    substantive=False,
                    source_ids=[],
                )
            )

        for document in documents:
            sentences.append(
                GroundedDraftSentence(
                    text=f"승인된 데모 지침에 따르면, {_first_sentence(document.body)}",
                    substantive=True,
                    source_ids=[document.id],
                )
            )

        if classification.missing_information:
            follow_up = (
                "정확한 처리를 위해 다음 정보의 추가 확인이 필요합니다: "
                + ", ".join(classification.missing_information)
                + "."
            )
        elif documents:
            follow_up = "담당자가 민원 내용과 적용 가능한 지침을 직접 확인합니다."
        else:
            follow_up = "일치하는 승인·유효 지침이 없어 담당자의 직접 검토가 필요합니다."
        sentences.append(
            GroundedDraftSentence(
                text=follow_up,
                substantive=False,
                source_ids=[],
            )
        )
        sentences.append(
            GroundedDraftSentence(
                text=(
                    "본 문구는 시연용 시스템의 초안이며 실제 성남시 처리 완료나 "
                    "법적 판단을 의미하지 않습니다."
                ),
                substantive=False,
                source_ids=[],
            )
        )
        return StructuredDraftOutput(provider=self.provider_name, sentences=sentences)


class CitationEnforcedDrafter:
    def __init__(
        self,
        catalog: DepartmentCatalog,
        provider: DraftProvider | None = None,
    ) -> None:
        self.provider = provider or RulesGroundedDraftProvider(catalog)

    @staticmethod
    def _rejection_reason(
        sentence: GroundedDraftSentence,
        documents_by_id: dict[str, KnowledgeDocument],
    ) -> str | None:
        violation = content_safety_violation(sentence.text)
        if violation:
            return f"unsafe_sentence:{violation}"
        if not sentence.substantive:
            if sentence.source_ids:
                return "non_substantive_sentence_has_sources"
            if not any(
                pattern.fullmatch(sentence.text) for pattern in _OPERATIONAL_SENTENCE_PATTERNS
            ):
                return "unrecognized_non_substantive_sentence"
            return None
        if not sentence.source_ids:
            return "missing_source_mapping"

        unknown_sources = sorted(set(sentence.source_ids) - documents_by_id.keys())
        if unknown_sources:
            return "unknown_source_mapping:" + ",".join(unknown_sources)

        sentence_tokens = tokenize(sentence.text)
        source_tokens: set[str] = set()
        for source_id in sentence.source_ids:
            document = documents_by_id[source_id]
            source_tokens.update(tokenize(f"{document.title} {document.body}"))
        overlap = sentence_tokens & source_tokens
        coverage = len(overlap) / max(1, len(sentence_tokens))
        if len(overlap) < 2 or coverage < 0.35:
            return "insufficient_lexical_support"
        return None

    def generate(
        self,
        *,
        title: str,
        location_text: str | None,
        classification: ClassificationResult,
        documents: list[KnowledgeDocument],
    ) -> DraftResult:
        structured = self.provider.generate(
            title=title,
            location_text=location_text,
            classification=classification,
            documents=documents,
        )
        documents_by_id = {document.id: document for document in documents}
        accepted: list[GroundedDraftSentence] = []
        rejected: list[RejectedDraftSentence] = []
        for sentence in structured.sentences:
            output_redaction = redact_pii(sentence.text)
            safe_sentence = sentence.model_copy(update={"text": output_redaction.text})
            reason: str | None
            if output_redaction.detected_types:
                reason = "pii_in_draft_output:" + ",".join(sorted(output_redaction.detected_types))
            else:
                reason = self._rejection_reason(safe_sentence, documents_by_id)
            if reason:
                rejected.append(
                    RejectedDraftSentence(**safe_sentence.model_dump(mode="python"), reason=reason)
                )
            else:
                accepted.append(safe_sentence)

        cited_source_ids = sorted(
            {source_id for sentence in accepted for source_id in sentence.source_ids}
        )
        if rejected:
            validation_status = "flagged"
        elif not cited_source_ids:
            validation_status = "no_sources"
        else:
            validation_status = "grounded"

        rendered_sentences = []
        for sentence in accepted:
            citation_suffix = "".join(f" [{source_id}]" for source_id in sentence.source_ids)
            rendered_sentences.append(sentence.text + citation_suffix)

        return DraftResult(
            text="\n\n".join(rendered_sentences),
            provider=structured.provider,
            validation_status=validation_status,
            sentences=accepted,
            rejected_sentences=rejected,
            source_ids=cited_source_ids,
            requires_human_review=bool(rejected) or not cited_source_ids,
        )
