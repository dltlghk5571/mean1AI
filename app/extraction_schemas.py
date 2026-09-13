"""Extractive suggestions only; the application and citizen own all draft changes."""

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.intake_schemas import IntakeTemplate
from app.services.pii import contains_direct_identifiers

MAX_REQUEST_BYTES = 80_000
MAX_RESPONSE_BYTES = 12_000
Quote = Annotated[str, Field(min_length=1, max_length=500)]


class ExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, str_strip_whitespace=True)


class PurposeSuggestion(ExtractionModel):
    value: Literal["complaint", "information"]
    quote: Quote


class TopicSuggestion(ExtractionModel):
    template_id: str = Field(pattern=r"^[a-z-]{1,40}$")
    quote: Quote


class LocationSuggestion(ExtractionModel):
    value: str = Field(min_length=1, max_length=300)
    quote: Quote


class AnswerSuggestion(ExtractionModel):
    field_id: str = Field(pattern=r"^[a-z_]{1,40}$")
    value: str = Field(min_length=1, max_length=500)
    quote: Quote


class ExtractionProposal(ExtractionModel):
    abstained: bool
    reason: Literal["supported", "ambiguous", "multiple_issues", "insufficient_information"]
    purpose: PurposeSuggestion | None
    topic: TopicSuggestion | None
    location: LocationSuggestion | None
    answers: list[AnswerSuggestion] = Field(max_length=4)

    @model_validator(mode="after")
    def validate_abstention(self) -> "ExtractionProposal":
        if self.abstained:
            if self.reason == "supported" or any(
                (self.purpose, self.topic, self.location, self.answers)
            ):
                raise ValueError("abstention_must_not_propose_values")
        elif self.reason != "supported" or self.purpose is None:
            raise ValueError("proposal_requires_supported_purpose")
        if self.answers and self.topic is None:
            raise ValueError("answers_require_topic")
        return self


class ExtractionRequest(ExtractionModel):
    schema_version: Literal["1"] = "1"
    task: Literal["extract_intake"] = "extract_intake"
    request_id: UUID
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1, max_length=120)
    source_text: str = Field(min_length=5, max_length=4000)
    templates: list[IntakeTemplate] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_ids(self) -> "ExtractionRequest":
        ids = [item.id for item in self.templates]
        if len(ids) != len(set(ids)) or any(
            len({question.field_id for question in item.questions}) != len(item.questions)
            for item in self.templates
        ):
            raise ValueError("duplicate_template_or_question")
        return self


class ExtractionResponse(ExtractionModel):
    schema_version: Literal["1"] = "1"
    request_id: UUID
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1, max_length=120)
    execution_mode: Literal["model", "synthetic"]
    proposal: ExtractionProposal


class PendingExtraction(ExtractionResponse):
    provider: Literal["demo", "club"]


def input_fingerprint(source_text: str, templates: list[IntakeTemplate]) -> str:
    value = {
        "contract": "extract-intake-v1",
        "source_text": source_text,
        "templates": [item.model_dump() for item in templates],
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_proposal(request: ExtractionRequest, proposal: ExtractionProposal) -> None:
    if contains_direct_identifiers(proposal.model_dump()):
        raise ValueError("extraction_contains_identifier")
    if proposal.abstained:
        return
    assert proposal.purpose is not None
    suggestions: list[
        PurposeSuggestion | AnswerSuggestion | TopicSuggestion | LocationSuggestion
    ] = [proposal.purpose, *proposal.answers]
    if proposal.topic:
        suggestions.append(proposal.topic)
    if proposal.location:
        suggestions.append(proposal.location)
    if any(item.quote not in request.source_text for item in suggestions):
        raise ValueError("extraction_quote_not_in_source")
    # No address completion, paraphrased facts, or invented answers. Keep exact user wording.
    values: list[AnswerSuggestion | LocationSuggestion] = list(proposal.answers)
    if proposal.location:
        values.append(proposal.location)
    for value in values:
        if value.value != value.quote or any(
            marker in value.value for marker in ("[전화번호]", "[이메일]", "[주민등록번호]")
        ):
            raise ValueError("extracted_value_must_equal_source_quote")
    if proposal.topic:
        template = next(
            (item for item in request.templates if item.id == proposal.topic.template_id), None
        )
        if template is None:
            raise ValueError("unknown_extraction_topic")
        questions = {item.field_id: item for item in template.questions}
        ids = [item.field_id for item in proposal.answers]
        if len(ids) != len(set(ids)) or any(identifier not in questions for identifier in ids):
            raise ValueError("unknown_or_duplicate_answer")
        # Safety-choice answers must be explicitly selected by the citizen, never inferred.
        if any(questions[item.field_id].choices_only for item in proposal.answers):
            raise ValueError("safety_choices_require_explicit_answer")
