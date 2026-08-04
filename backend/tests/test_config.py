"""Settings must ignore blank env entries and fall back to field defaults.

Regression guard: `.env.example` ships a literal `DQ_DATABASE_URL=` (blank).
Without ``env_ignore_empty`` pydantic-settings treats that empty string as a
real value and overrides the built-in sqlite default with "", which then fails
``create_engine`` ("Could not parse SQLAlchemy URL"). The documented
"copy .env.example -> .env and run" flow depends on blanks meaning "unset".
"""

import jwt
import pytest
from pydantic import ValidationError

from app.config import DEFAULT_ANTHROPIC_MODEL, INSECURE_SECRET_KEYS, Settings

STRONG_SECRET = "S3cure-prod-" + "x" * 32  # >= 32 chars, not a known default


def test_blank_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DQ_DATABASE_URL", "")
    # _env_file=None isolates the test from the repo's real .env file.
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("sqlite:///")


def test_explicit_env_value_is_honored(monkeypatch):
    monkeypatch.setenv("DQ_DATABASE_URL", "postgresql+psycopg://u:p@host:5432/db")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://u:p@host:5432/db"


# ---- #155: fail-fast on insecure config when DQ_ENV=prod ------------------------


def _prod_settings(**overrides):
    """Build prod Settings with strong defaults, overriding one field per test.

    Init kwargs outrank env vars in pydantic-settings, so the conftest's ambient
    DQ_SECRET_KEY / DQ_BOOTSTRAP_ADMIN_PASSWORD don't leak into these assertions.
    """
    base = {
        "env": "prod",
        "secret_key": STRONG_SECRET,
        "bootstrap_admin_password": "a-strong-bootstrap-pw",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


def test_prod_accepts_strong_config():
    s = _prod_settings()
    assert s.is_production
    assert s.secret_key == STRONG_SECRET


def test_dev_allows_insecure_defaults():
    # Default env is dev; the seeded/test defaults must still construct cleanly.
    s = Settings(
        secret_key="dev-only-secret-change-me",
        bootstrap_admin_password="admin123",
        _env_file=None,
    )
    assert not s.is_production


@pytest.mark.parametrize("bad_secret", sorted(INSECURE_SECRET_KEYS) + ["short"])
def test_prod_rejects_insecure_or_short_secret(bad_secret):
    with pytest.raises(ValidationError) as exc:
        _prod_settings(secret_key=bad_secret)
    assert "DQ_SECRET_KEY" in str(exc.value)


def test_prod_rejects_compose_fallback_secret_despite_length():
    # The docker-compose fallback is >= 32 chars, so a length check alone would
    # pass it — it must be caught by the explicit denylist.
    fallback = "change-me-in-prod-0123456789abcdef"
    assert len(fallback) >= 32
    with pytest.raises(ValidationError):
        _prod_settings(secret_key=fallback)


def test_prod_rejects_default_admin_password():
    with pytest.raises(ValidationError) as exc:
        _prod_settings(bootstrap_admin_password="admin123")
    assert "DQ_BOOTSTRAP_ADMIN_PASSWORD" in str(exc.value)


def test_default_secret_enables_admin_token_forgery():
    # Demonstrates WHY the guard exists: with the public default secret, a
    # hand-forged admin token verifies. (env=dev so Settings still constructs.)
    s = Settings(env="dev", secret_key="dev-only-secret-change-me", _env_file=None)
    forged = jwt.encode({"sub": "1", "role": "admin"}, s.secret_key, algorithm="HS256")
    decoded = jwt.decode(forged, s.secret_key, algorithms=["HS256"])
    assert decoded["role"] == "admin"  # forgery works -> prod must reject this secret


@pytest.mark.parametrize("bad_env", ["prd", "productionn", "staging", "qa", "dev!", "prod-eu"])
def test_unknown_env_value_is_rejected(bad_env):
    # An unrecognized DQ_ENV must fail fast, not silently fall back to dev and
    # skip the production guard (PR #161 review).
    with pytest.raises(ValidationError) as exc:
        Settings(env=bad_env, secret_key="dev-only-secret-change-me", _env_file=None)
    assert "DQ_ENV" in str(exc.value)


@pytest.mark.parametrize(
    "good_env", ["dev", "DEV", " dev ", "prod", "Prod", "production", " PRODUCTION "]
)
def test_known_env_values_accepted_case_insensitively(good_env):
    s = Settings(
        env=good_env,
        secret_key=STRONG_SECRET,
        bootstrap_admin_password="a-strong-bootstrap-pw",
        _env_file=None,
    )
    assert s.env == good_env  # stored verbatim; normalized only for comparisons
    assert s.is_production == (good_env.strip().lower() in ("prod", "production"))


# ---- #266: a configured LLM key must never be ignored silently -------------------

# Fake credentials for config resolution only — never sent anywhere.
FAKE_OPENAI_KEY = "sk-fake-openai-key-for-tests"
FAKE_ANTHROPIC_KEY = "sk-ant-fake-key-for-tests"


def _llm_settings(**overrides):
    """Settings with the LLM knobs pinned, isolated from ambient env/.env.

    Init kwargs outrank env vars in pydantic-settings, so a real ANTHROPIC_API_KEY /
    OPENROUTER_API_KEY on the developer's machine cannot change these outcomes.
    """
    base = {
        "llm_provider": "auto",
        "anthropic_api_key": "",
        "llm_api_key": "",
        "llm_model": "",
        "llm_base_url": "https://openrouter.ai/api/v1",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.parametrize("provider", ["openrouter", "openai", "auto"])
def test_key_set_but_model_unset_reports_a_specific_reason(provider):
    """The reported bug: a (possibly paid) key configured, DQ_LLM_MODEL empty."""
    s = _llm_settings(llm_provider=provider, llm_api_key=FAKE_OPENAI_KEY, llm_model="")
    assert s.llm_enabled is False  # every AI feature is off ...
    reason = s.llm_config_problem()
    assert reason is not None  # ... and now it says why
    assert "DQ_LLM_MODEL" in reason


def test_nothing_configured_is_not_a_problem():
    """No key at all is a supported mode (golden rule 4), not a misconfiguration."""
    s = _llm_settings()
    assert s.llm_enabled is False
    assert s.llm_config_problem() is None


@pytest.mark.parametrize(
    "kwargs",
    [
        # working OpenAI-compatible config
        {"llm_provider": "openrouter", "llm_api_key": FAKE_OPENAI_KEY, "llm_model": "some/model"},
        # working anthropic config (model defaulted)
        {"llm_provider": "anthropic", "anthropic_api_key": FAKE_ANTHROPIC_KEY},
        {"llm_provider": "auto", "anthropic_api_key": FAKE_ANTHROPIC_KEY},
    ],
)
def test_resolvable_config_has_no_problem(kwargs):
    s = _llm_settings(**kwargs)
    assert s.llm_enabled is True
    assert s.llm_config_problem() is None


def test_anthropic_still_defaults_its_model():
    # The asymmetry is deliberate and kept: dropping this default would disable a
    # currently-working ANTHROPIC_API_KEY-only deployment.
    s = _llm_settings(llm_provider="anthropic", anthropic_api_key=FAKE_ANTHROPIC_KEY)
    assert s.resolved_llm()["model"] == DEFAULT_ANTHROPIC_MODEL


def test_openai_provider_with_only_anthropic_key_names_the_missing_var():
    s = _llm_settings(
        llm_provider="openai", anthropic_api_key=FAKE_ANTHROPIC_KEY, llm_model="some/model"
    )
    reason = s.llm_config_problem()
    assert reason is not None
    assert "DQ_LLM_API_KEY" in reason


def test_anthropic_provider_with_only_openai_key_names_the_missing_var():
    s = _llm_settings(llm_provider="anthropic", llm_api_key=FAKE_OPENAI_KEY, llm_model="m")
    reason = s.llm_config_problem()
    assert reason is not None
    assert "ANTHROPIC_API_KEY" in reason


def test_unknown_provider_with_a_key_is_reported():
    # DQ_LLM_PROVIDER is not fail-fast validated (the LLM is optional), so a typo
    # would otherwise disable AI features silently.
    s = _llm_settings(llm_provider="anthropi", llm_api_key=FAKE_OPENAI_KEY, llm_model="m")
    assert s.llm_enabled is False
    reason = s.llm_config_problem()
    assert reason is not None
    assert "DQ_LLM_PROVIDER" in reason


def test_empty_base_url_with_a_key_is_reported():
    s = _llm_settings(
        llm_provider="openrouter", llm_api_key=FAKE_OPENAI_KEY, llm_model="m", llm_base_url=""
    )
    reason = s.llm_config_problem()
    assert reason is not None
    assert "DQ_LLM_BASE_URL" in reason


@pytest.mark.parametrize(
    "kwargs",
    [
        {"llm_provider": "openrouter", "llm_api_key": FAKE_OPENAI_KEY},
        {"llm_provider": "anthropi", "llm_api_key": FAKE_OPENAI_KEY, "llm_model": "m"},
        {"llm_provider": "openai", "anthropic_api_key": FAKE_ANTHROPIC_KEY, "llm_model": "m"},
        {"llm_provider": "anthropic", "llm_api_key": FAKE_OPENAI_KEY, "llm_model": "m"},
        {
            "llm_provider": "openrouter",
            "llm_api_key": FAKE_OPENAI_KEY,
            "llm_model": "m",
            "llm_base_url": "",
        },
    ],
)
def test_reason_never_leaks_key_material(kwargs):
    """The reason reaches an unauthenticated /health — it must name env vars only."""
    reason = _llm_settings(**kwargs).llm_config_problem()
    assert reason is not None
    for key in (FAKE_OPENAI_KEY, FAKE_ANTHROPIC_KEY):
        assert key not in reason
        for n in (6, 8, 12):  # not even a prefix of the secret
            assert key[:n] not in reason


def test_health_exposes_the_reason_field(client):
    """/health must distinguish "no key" from "key set but unusable" (#266).

    The test env configures no keys, so the reason is null here; the field's
    presence is the API contract mirrored in frontend/src/api/types.ts.
    """
    body = client.get("/api/v1/health").json()
    assert body["llm_enabled"] is False
    assert "llm_disabled_reason" in body
    assert body["llm_disabled_reason"] is None
