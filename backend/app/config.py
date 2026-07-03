"""Application settings, loaded from environment / .env (prefix DQ_)."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

# Known-insecure defaults that must never run in production (#155). The
# docker-compose fallback "change-me-in-prod-0123456789abcdef" is >= 32 chars,
# so a length check alone would not catch it — it must be denylisted explicitly.
INSECURE_SECRET_KEYS = frozenset(
    {"dev-only-secret-change-me", "change-me-in-prod-0123456789abcdef"}
)
INSECURE_ADMIN_PASSWORDS = frozenset({"admin123"})
MIN_SECRET_KEY_LENGTH = 32

# Recognized deployment modes. Unknown values are rejected (not treated as dev),
# so a typo in DQ_ENV cannot silently disable the production security checks.
PROD_ENVS = frozenset({"prod", "production"})
ALLOWED_ENVS = frozenset({"dev"}) | PROD_ENVS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DQ_",
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        # Treat blank entries (e.g. a `DQ_DATABASE_URL=` left in a copied
        # .env.example) as unset so they fall back to the field defaults below,
        # instead of overriding them with "" (which broke engine creation).
        env_ignore_empty=True,
    )

    # App metadata database (SQLite for dev, PostgreSQL for prod)
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'dqsentinel.db').as_posix()}"

    # App-DB engine timeouts (#158). A degraded app DB must fail fast, not hang
    # every API thread + the worker indefinitely. Applied to the PostgreSQL engine
    # via connect_args + pool_timeout; SQLite uses WAL + busy_timeout instead. Set
    # a *_ms value to 0 to disable that particular server-side timeout.
    db_statement_timeout_ms: int = 30_000
    db_idle_in_tx_timeout_ms: int = 60_000
    db_connect_timeout_seconds: int = 10
    db_pool_timeout_seconds: int = 30

    # Deployment environment: `dev` (default) keeps local/test flows frictionless;
    # `prod` turns on the fail-fast security validation below (#155). Set DQ_ENV=prod
    # for any non-local deployment.
    env: str = "dev"  # dev | prod

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
    anthropic_api_key: str = Field(
        default="", validation_alias=AliasChoices("anthropic_api_key", "ANTHROPIC_API_KEY")
    )
    llm_api_key: str = Field(
        default="", validation_alias=AliasChoices("llm_api_key", "DQ_LLM_API_KEY", "OPENROUTER_API_KEY")
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

    # Built-in data catalog (one-click sample datasets, see app/catalog/). The
    # backing SQLite/DuckDB files are generated lazily on connect under this dir,
    # which is gitignored. Empty -> <repo>/samples/catalog.
    catalog_data_dir: str = ""

    # Profiling / execution limits
    profile_sample_rows: int = 50_000
    exception_sample_rows: int = 50
    agent_query_row_limit: int = 200
    ml_max_rows: int = 50_000

    # Worker
    worker_poll_seconds: int = 15
    worker_concurrency: int = 4
    worker_metrics_port: int = 9100
    # SLA evaluation cadence (#102): the worker recomputes SLA rollups at most
    # this often (a row per SLA per pass, so don't set it as low as the poll).
    sla_eval_seconds: int = 300

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
    webhook_url: str = ""  # generic incident webhook default (rules may override target)
    webhook_hmac_secret: str = ""  # signs generic webhook payloads when configured
    teams_webhook_url: str = ""  # Microsoft Teams incoming webhook default
    pagerduty_routing_key: str = ""
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""
    jira_issue_type: str = "Bug"
    servicenow_instance_url: str = ""
    servicenow_user: str = ""
    servicenow_password: str = ""
    servicenow_assignment_group: str = ""

    # Audit log retention (issue #30): rows older than this are purged by a
    # daily pass in the worker. 0 disables purging (keep everything).
    audit_retention_days: int = 365

    # Observability
    log_format: str = "text"  # text | json
    log_level: str = "INFO"

    # In-app documentation browser (the /docs page). Markdown is read read-only
    # from this directory. Empty -> <repo>/docs in dev, which resolves to /docs
    # inside the backend image (mount ./docs:/docs:ro in docker-compose).
    docs_dir: str = ""

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return self.resolved_llm() is not None

    @property
    def docs_path(self) -> Path:
        """Directory the in-app docs browser reads markdown from."""
        return Path(self.docs_dir) if self.docs_dir else (REPO_DIR / "docs")

    @property
    def catalog_path(self) -> Path:
        """Directory the built-in data catalog generates its backing DB files in."""
        return Path(self.catalog_data_dir) if self.catalog_data_dir else (REPO_DIR / "samples" / "catalog")

    @property
    def is_production(self) -> bool:
        return self.env.strip().lower() in PROD_ENVS

    @model_validator(mode="after")
    def _enforce_secure_production(self) -> "Settings":
        """Fail fast on insecure defaults when DQ_ENV=prod (#155).

        A deploy that forgets DQ_SECRET_KEY would otherwise sign JWTs with the
        public repo default, letting anyone forge an admin token. Refuse to boot
        rather than run silently wide-open. `dev` stays permissive so local and
        test flows are unaffected.
        """
        mode = self.env.strip().lower()
        if mode not in ALLOWED_ENVS:
            raise ValueError(
                f"DQ_ENV must be one of {sorted(ALLOWED_ENVS)} (case-insensitive); "
                f"got {self.env!r}. Unknown values are rejected so a typo cannot "
                "silently disable the production security checks."
            )
        if mode not in PROD_ENVS:
            return self
        problems: list[str] = []
        if self.secret_key in INSECURE_SECRET_KEYS or len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            problems.append(
                "DQ_SECRET_KEY is a known default or shorter than "
                f"{MIN_SECRET_KEY_LENGTH} chars — set a strong random value, e.g. "
                '`python -c "import secrets; print(secrets.token_urlsafe(48))"`.'
            )
        if self.bootstrap_admin_password in INSECURE_ADMIN_PASSWORDS:
            problems.append(
                "DQ_BOOTSTRAP_ADMIN_PASSWORD is the insecure default — set a strong "
                "bootstrap admin password."
            )
        if problems:
            raise ValueError(
                "Refusing to start with DQ_ENV=prod and insecure configuration:\n  - "
                + "\n  - ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
