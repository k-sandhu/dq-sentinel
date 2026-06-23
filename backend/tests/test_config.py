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

from app.config import INSECURE_SECRET_KEYS, Settings

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
