from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "성남 민원 AI 코파일럿 — 데모"
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./civic_ai.db"
    ai_provider: Literal["rules", "openai"] = "rules"
    chat_provider: Literal["demo", "agent_demo", "club", "unavailable"] = "agent_demo"
    chat_endpoint_url: str | None = None
    chat_model_id: str | None = Field(default=None, max_length=120)
    chat_api_key: SecretStr | None = None
    chat_request_timeout_seconds: float = Field(default=15, ge=1, le=30)
    chat_turn_timeout_seconds: float = Field(default=30, ge=1, le=60)
    chat_max_concurrent: int = Field(default=4, ge=1, le=16)
    ai_deferred_enabled: bool = False
    ai_queue_max_attempts: int = Field(default=3, ge=1, le=10)
    ai_queue_retry_seconds: int = Field(default=30, ge=1, le=3600)
    ai_queue_lease_seconds: int = Field(default=120, ge=60, le=3600)
    session_secret: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6"
    auto_route_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    @field_validator("chat_endpoint_url")
    @classmethod
    def validate_chat_endpoint(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        parsed = urlsplit(value)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.hostname
            or parsed.scheme not in {"http", "https"}
            or (parsed.scheme == "http" and not local)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or "\\" in value
            or any(char.isspace() for char in value)
        ):
            raise ValueError("invalid_chat_endpoint")
        _ = parsed.port
        return value

    @model_validator(mode="after")
    def require_club_configuration(self) -> "Settings":
        if self.chat_provider == "club" and (
            not self.chat_endpoint_url
            or not self.chat_model_id
            or not self.chat_model_id.strip()
            or not self.chat_api_key
            or not self.chat_api_key.get_secret_value().strip()
        ):
            raise ValueError("club_endpoint_model_and_key_required")
        return self

    @property
    def package_dir(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def departments_path(self) -> Path:
        return self.package_dir / "data" / "departments.json"

    @property
    def knowledge_dir(self) -> Path:
        return self.package_dir / "data" / "knowledge"


@lru_cache
def get_settings() -> Settings:
    return Settings()
