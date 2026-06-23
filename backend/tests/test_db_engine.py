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


# --- migration advisory lock must not be bounded by the #158 timeouts ----------


class _FakeConn:
    def __init__(self, log: list[str]):
        self.log = log

    def execute(self, stmt, params=None):
        self.log.append(str(stmt))

    def begin(self):
        class _Tx:
            def __enter__(self_inner):
                return None

            def __exit__(self_inner, *exc):
                return False

        return _Tx()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self):
        self.log: list[str] = []

    def connect(self):
        return _FakeConn(self.log)


def test_pg_migration_lock_disables_timeouts_before_blocking_lock():
    from app.db import _with_pg_migration_lock

    eng = _FakeEngine()
    ran = []
    _with_pg_migration_lock(eng, lambda: ran.append("migrated"))

    assert ran == ["migrated"]
    sql = eng.log
    lock_i = next(i for i, s in enumerate(sql) if "pg_advisory_lock" in s)
    # Both per-connection timeouts are disabled BEFORE the blocking advisory lock,
    # so a sibling holding the lock past statement_timeout can't crash startup.
    assert sql.index("SET LOCAL statement_timeout = 0") < lock_i
    assert sql.index("SET LOCAL idle_in_transaction_session_timeout = 0") < lock_i
    assert any("pg_advisory_unlock" in s for s in sql)


def test_pg_migration_lock_unlocks_on_error():
    from app.db import _with_pg_migration_lock

    def _boom():
        raise RuntimeError("migration failed")

    eng = _FakeEngine()
    with pytest.raises(RuntimeError):
        _with_pg_migration_lock(eng, _boom)
    assert any("pg_advisory_unlock" in s for s in eng.log)  # released even on failure
