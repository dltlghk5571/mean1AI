"""Version 1 chat contracts. No credentials or citizen identity enter provider context."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.service_data_schemas import ServiceCard

Stage = Literal[
    "welcome", "intent", "description", "location", "review", "information", "submitted"
]
Action = Literal["say", "complaint", "information", "skip_location", "edit", "reset", "confirm"]


class ChatModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatMessage(ChatModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=5000)


class ChatDraft(ChatModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=4000)
    location_text: str = Field(default="", max_length=300)


class ChatState(ChatModel):
    stage: Stage = "welcome"
    messages: list[ChatMessage] = Field(default_factory=list, max_length=40)
    draft: ChatDraft = Field(default_factory=ChatDraft)
    source_ids: list[Literal["bokjiro", "seongnam_handbook"]] = Field(default_factory=list)
    urgent: bool = False
    service_cards: list[ServiceCard] = Field(default_factory=list, max_length=3)


class ChatTurn(ChatModel):
    # Same bounded, string-valued JSON envelope as the existing citizen form API.
    revision: str = Field(pattern=r"^(0|[1-9][0-9]{0,8})$")
    request_id: str
    action: Action
    message: str = Field(default="", max_length=4000)
    title: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=4000)
    location_text: str = Field(default="", max_length=300)
    consent: Literal["", "yes"] = ""

    @field_validator("request_id")
    @classmethod
    def valid_request_id(cls, value: str) -> str:
        return str(UUID(value))


class AgentContext(ChatModel):
    schema_version: Literal["1"] = "1"
    state: ChatState
    action: Action
    expected_stage: Stage


class AgentReply(ChatModel):
    schema_version: Literal["1"] = "1"
    next_stage: Stage
    message: str = Field(min_length=1, max_length=2000)
    source_ids: list[Literal["bokjiro", "seongnam_handbook"]] = Field(default_factory=list)
