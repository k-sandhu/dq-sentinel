"""App-DB engine hardening (#158): the PostgreSQL engine must bound a degraded DB
with connect / statement / idle / pool timeouts so a hung app DB fails fast
instead of freezing every API thread and the worker. SQLite (dev/test) keeps its
WAL + busy_timeout setup unchanged.
"""

import pytest

from app.config import Settings
from app.db import _build_engine, _pg_connect_args


def _settings(**over) -> Settings:
    base = {"env": "dev", "secret_key": "x" * 40, "_env_file": None}
    base.update(over)
    return Settings(**base)


def test_pg_connect_args_defaults():
    args = _pg_connect_args(_settings())
    assert args["connect_timeout"] == 10
    assert "statement_timeout=30000" in args["options"]
    assert "idle_in_transaction_session_timeout=60000" in args["options"]


def test_pg_connect_args_custom_values():
    args = _pg_connect_args(
        _settings(
            db_statement_timeout_ms=5000,
            db_idle_in_tx_timeout_ms=15000,
            db_connect_timeout_seconds=3,
        )
    )
    assert args["connect_timeout"] == 3
    assert "statement_timeout=5000" in args["options"]
    assert "idle_in_transaction_session_timeout=15000" in args["options"]


def test_pg_connect_args_zero_disables_server_timeouts():
    args = _pg_connect_args(_settings(db_statement_timeout_ms=0, db_idle_in_tx_timeout_ms=0))
    assert "options" not in args  # both server-side timeouts disabled
    assert args["connect_timeout"] == 10  # connect timeout still applied


def test_sqlite_engine_still_builds():
    engine = _build_engine("sqlite://", _settings())
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()


def test_postgres_engine_wires_pool_and_connect_timeouts():
    # Needs the psycopg dialect importable; create_engine is lazy so this never
    # opens a real connection to localhost.
    pytest.importorskip("psycopg")
    settings = _settings(db_pool_timeout_seconds=17)
    engine = _build_engine("postgresql+psycopg://u:p@localhost:5432/dqsentinel", settings)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.pool._timeout == 17  # pool_timeout wired through to QueuePool
    finally:
        engine.dispose()
