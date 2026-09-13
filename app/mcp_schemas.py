"""Versioned MCP tool payloads; credentials and private complaint IDs are not accepted."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.service_data_schemas import RequiredInformation, ServiceCard


class MCPModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class CatalogReference(MCPModel):
    version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    review_id: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SearchInput(MCPModel):
    query: str = Field(max_length=2000, description="개인정보를 제외한 생활불편·복지 검색어")
    limit: int = Field(default=3, ge=1, le=3)


class RequirementsInput(MCPModel):
    service_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    catalog: CatalogReference | None = None

    @model_validator(mode="after")
    def require_reference(self) -> Self:
        if (self.service_id is None) != (self.catalog is None):
            raise ValueError("service_id_and_catalog_required_together")
        return self


class CatalogToolResult(MCPModel):
    schema_version: Literal["1"] = "1"
    status: Literal[
        "ok", "catalog_unavailable", "stale_catalog", "not_found", "invalid_input", "error"
    ]
    message: str
    catalog: CatalogReference | None = None
    services: list[ServiceCard] = Field(default_factory=list, max_length=3)
    required_information: list[RequiredInformation] = Field(default_factory=list, max_length=20)

    @property
    def is_error(self) -> bool:
        return self.status in {"stale_catalog", "not_found", "invalid_input", "error"}
