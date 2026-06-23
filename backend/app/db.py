"""App-metadata database: lazy engine, session factory, init + bootstrap."""

import logging
from collections.abc import Callable, Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import BACKEND_DIR, Settings, get_settings

log = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - driver hook
    # WAL + busy_timeout make SQLite tolerate concurrent app/worker access
    # (and OneDrive's file-handle grabbing) far better.
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def _pg_connect_args(settings: Settings) -> dict[str, object]:
    """libpq connect args for the PostgreSQL app-DB engine (#158).

    Bounds a degraded app DB: ``connect_timeout`` caps the TCP/auth wait, and the
    server-side ``statement_timeout`` / ``idle_in_transaction_session_timeout``
    abort a hung query/transaction instead of parking an API thread or the worker
    poll loop forever. These apply only to the application engine; Alembic builds
    its own engine (``migrations/env.py``), so legitimate startup DDL is never
    bounded. Set a ``*_ms`` value to 0 to disable that server-side timeout.
    """
    opts: list[str] = []
    if settings.db_statement_timeout_ms > 0:
        opts.append(f"-c statement_timeout={settings.db_statement_timeout_ms}")
    if settings.db_idle_in_tx_timeout_ms > 0:
        opts.append(f"-c idle_in_transaction_session_timeout={settings.db_idle_in_tx_timeout_ms}")
    args: dict[str, object] = {"connect_timeout": settings.db_connect_timeout_seconds}
    if opts:
        args["options"] = " ".join(opts)
    return args


def _build_engine(url: str, settings: Settings) -> Engine:
    """Create the app-DB engine for ``url``. SQLite (dev/test) gets WAL + busy
    timeout; PostgreSQL (prod) gets connect/statement/idle/pool timeouts (#158)."""
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False}, pool_pre_ping=True)
        event.listen(engine, "connect", _set_sqlite_pragma)
        return engine
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_timeout=settings.db_pool_timeout_seconds,
        connect_args=_pg_connect_args(settings),
    )


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = _build_engine(settings.database_url, settings)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session."""
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


# Arbitrary constant key for the Postgres advisory lock that serializes startup
# migrations across the concurrently-booting api and worker processes.
_MIGRATION_LOCK_KEY = 0x6451_5343  # "dQSC"


def _with_pg_migration_lock(engine: Engine, run: Callable[[], None]) -> None:
    """Run ``run()`` while holding the Postgres migration advisory lock (#158).

    ``pg_advisory_lock`` is a *blocking* wait and runs on the application engine —
    whose connections carry the #158 ``statement_timeout`` and
    ``idle_in_transaction_session_timeout``. A sibling booting concurrently can
    hold the lock (or the DDL can run) longer than those timeouts, which would
    cancel the waiter / terminate the lock holder and crash startup. Disable both
    timeouts on this connection for the lock transaction (``SET LOCAL`` auto-resets
    at transaction end) so migration startup is never bounded by request-path
    timeouts. The lock is always released, even if ``run()`` raises.
    """
    with engine.connect() as conn, conn.begin():
        conn.execute(text("SET LOCAL statement_timeout = 0"))
        conn.execute(text("SET LOCAL idle_in_transaction_session_timeout = 0"))
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _MIGRATION_LOCK_KEY})
        try:
            run()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK_KEY})


def _run_migrations(engine: Engine) -> None:
    """Bring the app DB schema to head via Alembic (issue #23; see backend/migrations/).

    Adoption path: a database created by the pre-Alembic ``create_all`` path
    already has the full current schema but no ``alembic_version`` table — stamp
    it at head rather than re-applying the baseline (which would fail on existing
    tables). Everything else (fresh DBs, already-migrated DBs) runs ``upgrade head``.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)

    def _migrate() -> None:
        insp = inspect(engine)
        if insp.has_table("users") and not insp.has_table("alembic_version"):
            command.stamp(cfg, "head")
        else:
            command.upgrade(cfg, "head")

    # api + worker boot concurrently in docker-compose and both call init_db().
    # On Postgres, hold a session advisory lock so only one runs DDL at a time;
    # the other then sees `head` and no-ops. SQLite (dev/test) is single-writer.
    if engine.dialect.name == "postgresql":
        _with_pg_migration_lock(engine, _migrate)
    else:
        _migrate()


def init_db() -> None:
    """Migrate the app DB to head and seed the bootstrap admin if no users exist."""
    from app import models
    from app.security import hash_password

    engine = get_engine()
    _run_migrations(engine)

    settings = get_settings()
    with session_factory()() as db:
        if db.query(models.User).count() == 0:
            admin = models.User(
                email=settings.bootstrap_admin_email,
                name="Admin",
                password_hash=hash_password(settings.bootstrap_admin_password),
                role="admin",
            )
            db.add(admin)
            db.commit()
            log.warning("Seeded bootstrap admin %s (change this password!)", admin.email)


def reset_for_tests() -> None:
    """Drop cached engine/session so tests can repoint DQ_DATABASE_URL."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
    get_settings.cache_clear()
