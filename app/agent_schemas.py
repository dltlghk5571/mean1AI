"""v2 planner contract: bounded read tools and server-owned terminal actions."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator

from app.chat_schemas import ChatDraft, ChatMessage, ChatModel, Stage
from app.service_data_schemas import RequiredInformation, ServiceCard
from app.services.pii import redact_pii


class ToolIdentity(ChatModel):
    call_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")

    @field_validator("call_id")
    @classmethod
    def reject_identifiers(cls, value: str) -> str:
        if redact_pii(value).detected_types:
            raise ValueError("tool_id_contains_personal_information")
        return value


class SearchServices(ToolIdentity):
    name: Literal["search_services"]
    query: str = Field(max_length=2000)
    limit: int = Field(default=3, ge=1, le=3)


class GetRequiredInformation(ToolIdentity):
    name: Literal["get_required_information"]
    service_id: str | None = Field(default=None, max_length=100)


ToolCall = Annotated[SearchServices | GetRequiredInformation, Field(discriminator="name")]


class ToolStep(ChatModel):
    kind: Literal["tool"]
    call: ToolCall


class AskStep(ChatModel):
    kind: Literal["ask"]
    field_id: Literal["content", "location_text"]


class AnswerStep(ChatModel):
    kind: Literal["answer"]
    service_ids: list[str] = Field(max_length=3)


class ReviewStep(ChatModel):
    kind: Literal["review"]


class ClarifyStep(ChatModel):
    kind: Literal["clarify"]


AgentStep = Annotated[
    ToolStep | AskStep | AnswerStep | ReviewStep | ClarifyStep, Field(discriminator="kind")
]
STEP_ADAPTER: TypeAdapter[AgentStep] = TypeAdapter(AgentStep)


class ToolObservation(ChatModel):
    call_id: str
    name: Literal["search_services", "get_required_information"]
    services: list[ServiceCard] = Field(default_factory=list)
    required_information: list[RequiredInformation] = Field(default_factory=list)
    catalog_available: bool


class PlanningContext(ChatModel):
    schema_version: Literal["2"] = "2"
    stage: Stage
    draft: ChatDraft
    messages: list[ChatMessage]
    observations: list[ToolObservation] = Field(default_factory=list)
    remaining_tool_calls: int = Field(ge=0, le=3)
