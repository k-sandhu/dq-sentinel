"""Application settings, loaded from environment / .env (prefix DQ_)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DQ_",
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App metadata database (SQLite for dev, PostgreSQL for prod)
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'dqsentinel.db').as_posix()}"

    # Auth
    secret_key: str = "dev-only-secret-change-me"
    access_token_hours: int = 12
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "admin123"

    # LLM (optional — features degrade gracefully without a key)
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    llm_model: str = "claude-opus-4-8"
    llm_max_explore_turns: int = 8
    llm_max_rca_turns: int = 12
    llm_max_output_tokens: int = 16000

    # Profiling / execution limits
    profile_sample_rows: int = 50_000
    exception_sample_rows: int = 50
    agent_query_row_limit: int = 200
    ml_max_rows: int = 50_000

    # Worker
    worker_poll_seconds: int = 15
    worker_concurrency: int = 4

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
