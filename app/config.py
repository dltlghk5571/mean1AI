from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "성남 민원 AI 코파일럿 — 데모"
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./civic_ai.db"
    ai_provider: Literal["rules", "openai"] = "rules"
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
    )

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
