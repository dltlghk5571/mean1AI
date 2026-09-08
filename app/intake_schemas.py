"""Server-owned optional dialogue templates, independent of official service catalogs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IntakeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntakeQuestion(IntakeModel):
    field_id: str = Field(pattern=r"^[a-z_]{1,40}$")
    label: str = Field(min_length=1, max_length=60)
    question: str = Field(min_length=1, max_length=400)
    choices: list[str] = Field(default_factory=list, max_length=4)
    choices_only: bool = False


class IntakeTemplate(IntakeModel):
    id: str = Field(pattern=r"^[a-z-]{1,40}$")
    version: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=60)
    purpose: Literal["complaint", "information"]
    questions: list[IntakeQuestion] = Field(min_length=1, max_length=4)


class IntakeAnswer(IntakeModel):
    status: Literal["answered", "skipped", "in_description", "not_asked"]
    value: str = Field(default="", max_length=500)


class IntakeState(IntakeModel):
    # Snapshot the product wording so an in-progress conversation survives a template update.
    template: IntakeTemplate
    answers: dict[str, IntakeAnswer] = Field(default_factory=dict, max_length=4)
    editing_field: str | None = None
    purpose: Literal["complaint", "information"]
