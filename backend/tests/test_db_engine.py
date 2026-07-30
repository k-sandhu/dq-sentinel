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


ABORTED_TX = "current transaction is aborted, commands ignored until end of transaction block"


class _FakeTx:
    """SQLAlchemy's ``Connection.begin()`` context manager."""

    def __init__(self, conn: "_FakeConn"):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_exc):
        self.conn.log.append("TX_ROLLBACK" if exc_type else "COMMIT")
        return False


class _FakeConn:
    """Stands in for a psycopg connection.

    Models the one Postgres behaviour this module has to survive: once the
    transaction is aborted, every further statement fails with
    "current transaction is aborted..." until the connection is rolled back.
    """

    def __init__(self, log: list[str]):
        self.log = log
        self.aborted = False

    def execute(self, stmt, params=None):
        if self.aborted:
            raise RuntimeError(ABORTED_TX)
        self.log.append(str(stmt))

    def begin(self):
        return _FakeTx(self)

    def rollback(self):
        self.aborted = False
        self.log.append("ROLLBACK")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self):
        self.log: list[str] = []
        self.conn = _FakeConn(self.log)  # one connection, so tests can poison it

    def connect(self):
        return self.conn


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


def test_pg_migration_lock_commits_lock_txn_before_running_the_migration():
    """#311: the advisory lock is session-scoped, so its transaction is committed
    before ``run()``. The connection then sits idle *outside* a transaction for the
    whole migration — nothing can leave it in an aborted state that poisons the
    unlock, and ``idle_in_transaction_session_timeout`` no longer applies to it."""
    from app.db import _with_pg_migration_lock

    seen_at_run_time: list[list[str]] = []
    eng = _FakeEngine()
    _with_pg_migration_lock(eng, lambda: seen_at_run_time.append(list(eng.log)))

    at_run = seen_at_run_time[0]
    assert any("pg_advisory_lock" in s for s in at_run)  # lock is held...
    assert "COMMIT" in at_run  # ...and its transaction is already committed


def test_pg_migration_lock_surfaces_original_error_not_the_aborted_transaction():
    """#311: init_db() runs ``upgrade head`` at startup — the worst place to lose
    the real message. If the failure aborted the transaction, the cleanup unlock
    would itself raise "current transaction is aborted..." and replace it."""
    from app.db import _with_pg_migration_lock

    eng = _FakeEngine()

    def _boom():
        eng.conn.aborted = True  # what Postgres does to the session on a failed stmt
        raise RuntimeError("column checks.threshold_kind does not exist")

    with pytest.raises(RuntimeError) as excinfo:
        _with_pg_migration_lock(eng, _boom)

    msg = str(excinfo.value)
    assert "column checks.threshold_kind does not exist" in msg
    assert "current transaction is aborted" not in msg
    # ...and the lock was still released, after rolling the aborted state back.
    unlock_i = next(i for i, s in enumerate(eng.log) if "pg_advisory_unlock" in s)
    assert eng.log.index("ROLLBACK") < unlock_i


def test_pg_migration_lock_swallows_an_unlock_failure_on_the_success_path(caplog):
    """A cleanup that cannot run is a log line, not a startup crash: the lock is
    session-scoped and dies with the connection closing moments later (#311)."""
    import logging

    from app.db import _with_pg_migration_lock

    class _UnlockRefuses(_FakeConn):
        def execute(self, stmt, params=None):
            if "pg_advisory_unlock" in str(stmt):
                raise RuntimeError("server closed the connection unexpectedly")
            return super().execute(stmt, params)

    eng = _FakeEngine()
    eng.conn = _UnlockRefuses(eng.log)

    with caplog.at_level(logging.WARNING, logger="app.db"):
        _with_pg_migration_lock(eng, lambda: eng.log.append("migrated"))

    assert "migrated" in eng.log
    assert any("advisory lock" in r.message for r in caplog.records)
