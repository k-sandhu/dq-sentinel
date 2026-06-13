"""App-metadata database: lazy engine, session factory, init + bootstrap."""

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

log = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        url = get_settings().database_url
        if url.startswith("sqlite"):
            _engine = create_engine(url, connect_args={"check_same_thread": False}, pool_pre_ping=True)

            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - driver hook
                # WAL + busy_timeout make SQLite tolerate concurrent app/worker access
                # (and OneDrive's file-handle grabbing) far better.
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()
        else:
            _engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)
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


def _ensure_columns(engine: Engine) -> None:
    """Poor-man's migration until Alembic lands (#23): add missing columns in place.

    `init_db()` only calls `create_all`, which will NOT add columns to existing
    tables — and the standing Docker demo runs Postgres with a persistent volume.
    So new ORM columns on existing tables need an explicit ALTER. Sibling issues
    (snooze, RCA report_json, ...) extend the `wanted` dict here.
    """
    wanted: dict[str, dict[str, str]] = {
        "exception_records": {
            # --- exceptions workbench: identity & recurrence (#55) ---
            "fingerprint": "VARCHAR(64)",
            "first_seen_at": "TIMESTAMP",  # TIMESTAMP (not DATETIME): valid on SQLite + Postgres
            "last_seen_at": "TIMESTAMP",
            "last_run_id": "INTEGER",
            "occurrence_count": "INTEGER DEFAULT 1",
            # --- exceptions workbench: triage workflow (#56) ---
            "assigned_to_id": "INTEGER",
        },
    }
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        # Backfill idempotently: historical rows predate these columns. Leave
        # `fingerprint` NULL (the runner only matches non-NULL fingerprints).
        if insp.has_table("exception_records"):
            conn.execute(
                text(
                    "UPDATE exception_records SET first_seen_at = created_at "
                    "WHERE first_seen_at IS NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE exception_records SET last_seen_at = created_at "
                    "WHERE last_seen_at IS NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE exception_records SET occurrence_count = 1 "
                    "WHERE occurrence_count IS NULL"
                )
            )


def init_db() -> None:
    """Create tables and seed the bootstrap admin if no users exist."""
    from app import models
    from app.security import hash_password

    engine = get_engine()
    models.Base.metadata.create_all(engine)
    _ensure_columns(engine)

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
