"""Application settings, loaded from environment / .env (prefix DQ_)."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DQ_",
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # App metadata database (SQLite for dev, PostgreSQL for prod)
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'dqsentinel.db').as_posix()}"

    # Auth
    secret_key: str = "dev-only-secret-change-me"
    access_token_hours: int = 12
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "admin123"

    # LLM (optional — features degrade gracefully without a key).
    # Provider-agnostic: "anthropic" uses the native Anthropic API;
    # "openai" / "openrouter" works with ANY OpenAI-compatible endpoint
    # (OpenRouter, vLLM, Ollama, Together, ...) via base_url + api_key.
    # "auto" picks anthropic if ANTHROPIC_API_KEY is set, else openai if
    # DQ_LLM_API_KEY is set.
    llm_provider: str = "auto"  # auto | anthropic | openai | openrouter
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    llm_api_key: str = Field(
        default="", validation_alias=AliasChoices("DQ_LLM_API_KEY", "OPENROUTER_API_KEY")
    )
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = ""  # default per provider: anthropic -> claude-opus-4-8; openai -> required
    llm_max_explore_turns: int = 8
    llm_max_rca_turns: int = 12
    llm_max_chat_turns: int = 10
    llm_max_output_tokens: int = 16000
    # Per-request HTTP timeout / retry budget for the LLM SDK clients. Without
    # this the SDKs wait up to 600s on a hung provider, which blocks a worker
    # thread and times out every proxy in front of the API.
    llm_timeout_seconds: float = 90.0
    llm_max_retries: int = 1

    def resolved_llm(self) -> dict | None:
        """Which provider/model will actually be used, or None when disabled."""
        provider = self.llm_provider.lower()
        if provider in ("openai", "openrouter") or (provider == "auto" and not self.anthropic_api_key and self.llm_api_key):
            if self.llm_api_key and self.llm_model and self.llm_base_url:
                return {
                    "provider": "openai",
                    "model": self.llm_model,
                    "base_url": self.llm_base_url,
                    "api_key": self.llm_api_key,
                }
            return None
        if provider in ("anthropic", "auto"):
            if self.anthropic_api_key:
                return {
                    "provider": "anthropic",
                    "model": self.llm_model or "claude-opus-4-8",
                    "base_url": None,
                    "api_key": self.anthropic_api_key,
                }
        return None

    # Profiling / execution limits
    profile_sample_rows: int = 50_000
    exception_sample_rows: int = 50
    agent_query_row_limit: int = 200
    ml_max_rows: int = 50_000

    # Worker
    worker_poll_seconds: int = 15
    worker_concurrency: int = 4
    worker_metrics_port: int = 9100

    # Notifications (issue #27 — Slack webhook + SMTP email). All optional:
    # with nothing set there are zero sends and zero behaviour change. Rules in
    # the DB (NotificationRule) decide *what* fires; these settings supply the
    # transport (and a global Slack default for rules that leave target blank).
    notify_slack_webhook_url: str = ""  # global default; a Slack rule may override per-target
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_addr: str = ""
    smtp_starttls: bool = True
    base_url: str = "http://localhost:3000"  # for building links in notification bodies

    # Audit log retention (issue #30): rows older than this are purged by a
    # daily pass in the worker. 0 disables purging (keep everything).
    audit_retention_days: int = 365

    # Observability
    log_format: str = "text"  # text | json
    log_level: str = "INFO"

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return self.resolved_llm() is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
