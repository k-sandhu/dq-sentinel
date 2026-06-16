"""Settings must ignore blank env entries and fall back to field defaults.

Regression guard: `.env.example` ships a literal `DQ_DATABASE_URL=` (blank).
Without ``env_ignore_empty`` pydantic-settings treats that empty string as a
real value and overrides the built-in sqlite default with "", which then fails
``create_engine`` ("Could not parse SQLAlchemy URL"). The documented
"copy .env.example -> .env and run" flow depends on blanks meaning "unset".
"""

from app.config import Settings


def test_blank_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DQ_DATABASE_URL", "")
    # _env_file=None isolates the test from the repo's real .env file.
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("sqlite:///")


def test_explicit_env_value_is_honored(monkeypatch):
    monkeypatch.setenv("DQ_DATABASE_URL", "postgresql+psycopg://u:p@host:5432/db")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://u:p@host:5432/db"
