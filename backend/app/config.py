from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server-side configuration. Every value comes from the environment or .env."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_prefix="RECOVERYOS_", extra="ignore"
    )

    database_url: str = "sqlite+pysqlite:///:memory:"
    cors_origins: str = "http://localhost:3000"
    max_request_bytes: int = 1_000_000
    rate_limit_per_minute: int = 120
    max_concurrent_runs: int = 5

    demo_mode: bool = True
    enable_real_github_writes: bool = False
    enable_github_link: bool = True
    github_token: str = Field(
        default="",
        validation_alias=AliasChoices("GITHUB_TOKEN", "RECOVERYOS_GITHUB_TOKEN"),
    )
    approval_ttl_minutes: int = 60
    max_step_attempts: int = 3
    retry_backoff_seconds: float = 0.5

    llm_provider: Literal["openrouter", "groq", "none"] = "none"
    llm_models: str = ""
    llm_timeout_seconds: float = 30.0
    llm_max_output_tokens: int = 2000
    llm_max_calls_per_run: int = 20

    openrouter_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "RECOVERYOS_OPENROUTER_API_KEY"),
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("OPENROUTER_BASE_URL", "RECOVERYOS_OPENROUTER_BASE_URL"),
    )
    groq_api_key: str = Field(
        default="", validation_alias=AliasChoices("GROQ_API_KEY", "RECOVERYOS_GROQ_API_KEY")
    )
    groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1",
        validation_alias=AliasChoices("GROQ_BASE_URL", "RECOVERYOS_GROQ_BASE_URL"),
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip() and o.strip() != "*"]

    @property
    def model_list(self) -> list[str]:
        return [m.strip() for m in self.llm_models.split(",") if m.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url.removeprefix("postgresql://")
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
