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

# Recognized DQ_LLM_PROVIDER values. Unlike DQ_ENV an unknown value here does not
# refuse to boot — the LLM is optional and must degrade gracefully (golden rule 4)
# — but it is reported through `Settings.llm_config_problem()` instead of silently
# leaving a configured key unused (#266).
LLM_PROVIDERS = frozenset({"auto", "anthropic", "openai", "openrouter"})
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"


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

    def _llm_route(self) -> str:
        """Which provider branch the current settings select: openai | anthropic | unknown.

        Shared by `resolved_llm()` and `llm_config_problem()` so the diagnosis can
        never describe a different branch than the one that actually ran.
        """
        provider = self.llm_provider.strip().lower()
        if provider in ("openai", "openrouter"):
            return "openai"
        if provider == "anthropic":
            return "anthropic"
        if provider == "auto":
            # auto prefers the native Anthropic path when ANTHROPIC_API_KEY is set,
            # and only takes the OpenAI-compatible path when DQ_LLM_API_KEY is the
            # only key present. With no key at all either branch resolves to None.
            return "openai" if (not self.anthropic_api_key and self.llm_api_key) else "anthropic"
        return "unknown"

    def resolved_llm(self) -> dict | None:
        """Which provider/model will actually be used, or None when disabled.

        Deliberate asymmetry: the anthropic path defaults the model, the
        OpenAI-compatible path requires one. `DQ_LLM_BASE_URL` may point at
        OpenRouter, vLLM, Ollama, Together, ... whose model ids share no
        namespace, so there is no default that is right anywhere — guessing one
        would turn a config mistake into a paid 400 at the first call instead of
        a clear "set DQ_LLM_MODEL". `llm_config_problem()` below is what makes
        that requirement visible rather than silent (#266).
        """
        route = self._llm_route()
        if route == "openai":
            if self.llm_api_key and self.llm_model and self.llm_base_url:
                return {
                    "provider": "openai",
                    "model": self.llm_model,
                    "base_url": self.llm_base_url,
                    "api_key": self.llm_api_key,
                }
            return None
        if route == "anthropic" and self.anthropic_api_key:
            return {
                "provider": "anthropic",
                "model": self.llm_model or DEFAULT_ANTHROPIC_MODEL,
                "base_url": None,
                "api_key": self.anthropic_api_key,
            }
        return None

    def llm_config_problem(self) -> str | None:
        """Why a configured LLM API key is going unused, or None (#266).

        None means "nothing to report": either the LLM resolved fine, or nothing
        is configured at all — an unconfigured LLM is a supported mode, not a
        problem. A non-None value means an API key IS set (possibly a paid one)
        and every AI feature is nevertheless off, which the operator has no other
        way to notice.

        The text is operator-facing (startup log, /health, Settings) and names
        only environment *variables* — never key material, not even a prefix.
        """
        if self.resolved_llm() is not None:
            return None
        if not (self.anthropic_api_key or self.llm_api_key):
            return None  # not configured at all — nothing to warn about

        route = self._llm_route()
        if route == "unknown":
            # Echo the offending value so the typo is obvious, but truncate and
            # repr() it: /health is unauthenticated, and this string also lands in
            # a log line.
            got = self.llm_provider[:32]
            return (
                f"An LLM API key is set but DQ_LLM_PROVIDER={got!r} is not a recognized "
                f"provider (expected one of {', '.join(sorted(LLM_PROVIDERS))}), so AI "
                "features are off."
            )
        if route == "openai":
            if not self.llm_api_key:
                return (
                    "DQ_LLM_PROVIDER selects the OpenAI-compatible path, which reads "
                    "DQ_LLM_API_KEY, but only ANTHROPIC_API_KEY is set — so AI features are "
                    "off. Set DQ_LLM_API_KEY, or set DQ_LLM_PROVIDER=anthropic to use the "
                    "key you already have."
                )
            if not self.llm_model:
                return (
                    "LLM key detected but DQ_LLM_MODEL is unset — set a model to enable AI "
                    "features. The OpenAI-compatible path has no default model because "
                    "model ids differ per endpoint (e.g. DQ_LLM_MODEL=anthropic/"
                    "claude-haiku-4.5 on OpenRouter)."
                )
            if not self.llm_base_url:
                return (
                    "LLM key detected but DQ_LLM_BASE_URL is empty — set the OpenAI-compatible "
                    "endpoint URL (e.g. https://openrouter.ai/api/v1) to enable AI features."
                )
        elif not self.anthropic_api_key:
            return (
                "DQ_LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY, but only DQ_LLM_API_KEY "
                "is set — so AI features are off. Set ANTHROPIC_API_KEY, or set "
                "DQ_LLM_PROVIDER=openai (or openrouter) to use the key you already have."
            )
        # Defensive: a key is set, resolution failed, and none of the specific
        # causes above matched. Still better than silence.
        return (
            "An LLM API key is set but the LLM configuration is incomplete, so AI features "
            "are off — check DQ_LLM_PROVIDER, DQ_LLM_MODEL and DQ_LLM_BASE_URL."
        )

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
