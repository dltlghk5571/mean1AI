from datetime import date
from pathlib import Path

from app.config import Settings
from app.schemas import (
    ClassificationCandidate,
    ClassificationResult,
    GroundedDraftSentence,
    StructuredDraftOutput,
)
from app.services.classifier import DepartmentCatalog
from app.services.draft import CitationEnforcedDrafter
from app.services.knowledge import KnowledgeDocument


class StaticDraftProvider:
    provider_name = "synthetic-static"

    def __init__(self, output: StructuredDraftOutput) -> None:
        self.output = output

    def generate(
        self,
        *,
        title: str,
        location_text: str | None,
        classification: ClassificationResult,
        documents: list[KnowledgeDocument],
    ) -> StructuredDraftOutput:
        del title, location_text, classification, documents
        return self.output


def _catalog() -> DepartmentCatalog:
    return DepartmentCatalog.from_json(Settings().departments_path)


def _classification() -> ClassificationResult:
    return ClassificationResult(
        category="streetlight",
        subcategory="가로등 점등 불량",
        candidates=[
            ClassificationCandidate(
                department_id="ROAD_LIGHTING",
                confidence=0.98,
                reason="합성 가로등 키워드",
            )
        ],
        evidence_summary="합성 평가 분류",
        provider="rules",
    )


def _document() -> KnowledgeDocument:
    return KnowledgeDocument(
        id="KB-SYNTH-LIGHT",
        title="가로등 합성 지침",
        category="streetlight",
        version="synthetic-1",
        effective_from=date(2026, 1, 1),
        effective_until=date(2099, 12, 31),
        approval_status="approved",
        superseded_by=None,
        body="가로등 고장은 위치와 관리번호를 확인한다.",
        path=Path("synthetic-light.md"),
    )


def _generate(output: StructuredDraftOutput, documents: list[KnowledgeDocument]):
    return CitationEnforcedDrafter(_catalog(), provider=StaticDraftProvider(output)).generate(
        title="합성 가로등 신고",
        location_text="가상 시험동",
        classification=_classification(),
        documents=documents,
    )


def test_valid_substantive_sentence_keeps_source_mapping_and_inline_citation() -> None:
    sentence = GroundedDraftSentence(
        text="가로등 고장은 위치와 관리번호를 확인한다.",
        substantive=True,
        source_ids=["KB-SYNTH-LIGHT"],
    )
    result = _generate(
        StructuredDraftOutput(provider="synthetic-static", sentences=[sentence]),
        [_document()],
    )

    assert result.validation_status == "grounded"
    assert result.sentences == [sentence]
    assert result.rejected_sentences == []
    assert result.source_ids == ["KB-SYNTH-LIGHT"]
    assert result.text.endswith("[KB-SYNTH-LIGHT]")
    assert result.requires_human_review is False


def test_missing_unknown_and_unsupported_source_mappings_are_rejected() -> None:
    output = StructuredDraftOutput(
        provider="synthetic-static",
        sentences=[
            GroundedDraftSentence(
                text="가로등 고장은 위치를 확인한다.",
                substantive=True,
                source_ids=[],
            ),
            GroundedDraftSentence(
                text="가로등 고장은 위치를 확인한다.",
                substantive=True,
                source_ids=["KB-UNKNOWN"],
            ),
            GroundedDraftSentence(
                text="모든 신고는 사흘 안에 반드시 해결된다.",
                substantive=True,
                source_ids=["KB-SYNTH-LIGHT"],
            ),
        ],
    )

    result = _generate(output, [_document()])

    assert result.validation_status == "flagged"
    assert result.sentences == []
    assert result.text == ""
    assert [item.reason for item in result.rejected_sentences] == [
        "missing_source_mapping",
        "unknown_source_mapping:KB-UNKNOWN",
        "insufficient_lexical_support",
    ]
    assert result.requires_human_review is True


def test_unsafe_provider_sentence_is_never_rendered_even_with_a_valid_source_id() -> None:
    unsafe_text = "담당자 검토 없이 민원을 자동으로 종결 처리한다."
    result = _generate(
        StructuredDraftOutput(
            provider="synthetic-static",
            sentences=[
                GroundedDraftSentence(
                    text=unsafe_text,
                    substantive=True,
                    source_ids=["KB-SYNTH-LIGHT"],
                )
            ],
        ),
        [_document()],
    )

    assert result.validation_status == "flagged"
    assert unsafe_text not in result.text
    assert result.rejected_sentences[0].reason == "unsafe_sentence:automatic_disposition"
    assert result.requires_human_review is True


def test_rules_provider_abstains_when_no_approved_relevant_source_is_available() -> None:
    result = CitationEnforcedDrafter(_catalog()).generate(
        title="합성 문의",
        location_text=None,
        classification=_classification(),
        documents=[],
    )

    assert result.provider == "rules"
    assert result.validation_status == "no_sources"
    assert result.source_ids == []
    assert result.requires_human_review is True
    assert "일치하는 승인·유효 지침이 없어" in result.text


def test_non_substantive_sentence_cannot_smuggle_a_source_mapping() -> None:
    result = _generate(
        StructuredDraftOutput(
            provider="synthetic-static",
            sentences=[
                GroundedDraftSentence(
                    text="단순 접수 문구입니다.",
                    substantive=False,
                    source_ids=["KB-SYNTH-LIGHT"],
                )
            ],
        ),
        [_document()],
    )

    assert result.sentences == []
    assert result.rejected_sentences[0].reason == "non_substantive_sentence_has_sources"


def test_provider_cannot_disguise_an_unsupported_claim_as_operational_text() -> None:
    unsupported_claim = "모든 신고는 사흘 안에 반드시 해결된다."
    result = _generate(
        StructuredDraftOutput(
            provider="synthetic-static",
            sentences=[
                GroundedDraftSentence(
                    text=unsupported_claim,
                    substantive=False,
                    source_ids=[],
                )
            ],
        ),
        [_document()],
    )

    assert unsupported_claim not in result.text
    assert result.rejected_sentences[0].reason == "unrecognized_non_substantive_sentence"


def test_provider_output_with_synthetic_identifier_is_rejected_and_redacted() -> None:
    synthetic_email = "draft-leak@example.com"
    result = _generate(
        StructuredDraftOutput(
            provider="synthetic-static",
            sentences=[
                GroundedDraftSentence(
                    text=f"연락 주소는 {synthetic_email}입니다.",
                    substantive=True,
                    source_ids=["KB-SYNTH-LIGHT"],
                )
            ],
        ),
        [_document()],
    )

    assert synthetic_email not in result.text
    assert synthetic_email not in result.rejected_sentences[0].text
    assert "[이메일]" in result.rejected_sentences[0].text
    assert result.rejected_sentences[0].reason == "pii_in_draft_output:email"
