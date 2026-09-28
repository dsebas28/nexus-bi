"""Typed application settings, read from environment variables or the .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore", case_sensitive=False)

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "nexus_bi"
    postgres_user: str
    postgres_password: str
    db_pool_min_size: int = Field(1, ge=1)
    db_pool_max_size: int = Field(10, ge=1)
    db_statement_timeout_ms: int = Field(15_000, ge=1_000)

    # AI analyst. "auto" = Claude if ANTHROPIC_API_KEY is set, else a local Ollama model if running,
    # else guided mode (a library of questions answered with hand-written SQL, no AI, free).
    ai_provider: Literal["auto", "anthropic", "ollama", "guided"] = "auto"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_num_gpu: int | None = None   # layers on the GPU; 0 forces CPU (e.g. when the NVIDIA driver is too old)
    anthropic_api_key: SecretStr | None = None
    ai_model: str = "claude-opus-5"
    ai_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    ai_rate_limit_per_minute: int = Field(6, ge=1)
    ai_access_token: SecretStr | None = None      # if set, POST /ai/ask requires the X-Access-Token header
    ai_db_user: str | None = None                 # dedicated read-only role (sql/roles.sql)
    ai_db_password: str | None = None

    # API
    environment: str = "development"
    api_cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    api_cache_ttl_seconds: int = Field(300, ge=0)
    log_level: str = "INFO"

    @field_validator("anthropic_api_key", "ai_access_token", "ai_db_user", "ai_db_password", "ollama_num_gpu",
                     mode="before")
    @classmethod
    def _blank_is_unset(cls, value):
        """`KEY=` in .env means 'not configured', not 'configured with an empty value'."""
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def database_conninfo(self) -> str:
        return (f"host={self.postgres_host} port={self.postgres_port} dbname={self.postgres_db} "
                f"user={self.postgres_user} password={self.postgres_password} application_name=nexus_bi_api")


@lru_cache
def get_settings() -> Settings:
    return Settings()
