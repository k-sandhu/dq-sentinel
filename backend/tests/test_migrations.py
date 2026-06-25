"""Migration/model drift guard (#23).

A fresh ``alembic upgrade head`` must produce exactly the same schema as
``Base.metadata.create_all``. If a model changes without a matching revision
(or vice-versa), this fails — which is the whole point of retiring the old
``_ensure_columns`` shim.
"""

import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.config import BACKEND_DIR
from app.models import Base


def _columns_by_table(engine) -> dict[str, set[str]]:
    insp = inspect(engine)
    return {
        t: {c["name"] for c in insp.get_columns(t)}
        for t in insp.get_table_names()
        if t != "alembic_version"
    }


def test_migrations_match_metadata():
    tmp = Path(tempfile.mkdtemp(prefix="dq-mig-"))
    mig_url = f"sqlite:///{(tmp / 'migrated.db').as_posix()}"
    meta_url = f"sqlite:///{(tmp / 'meta.db').as_posix()}"

    # A) schema built by the migration chain
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", mig_url)
    command.upgrade(cfg, "head")
    mig_engine = create_engine(mig_url)

    # B) schema built directly from the ORM metadata
    meta_engine = create_engine(meta_url)
    Base.metadata.create_all(meta_engine)

    mig = _columns_by_table(mig_engine)
    meta = _columns_by_table(meta_engine)

    assert set(mig) == set(meta), (
        f"table set differs — only-in-migration={set(mig) - set(meta)}, "
        f"only-in-metadata={set(meta) - set(mig)}"
    )
    for table, cols in meta.items():
        assert mig[table] == cols, (
            f"column drift in {table}: migration={mig[table]} metadata={cols} "
            "(add an alembic revision for the model change)"
        )

    mig_engine.dispose()
    meta_engine.dispose()


def test_check_version_backfill_seeds_v1():
    """0011 must backfill a v1 'baseline' snapshot for every check that already
    existed before the migration, so history/restore work immediately (#185)."""
    tmp = Path(tempfile.mkdtemp(prefix="dq-cvbf-"))
    url = f"sqlite:///{(tmp / 'bf.db').as_posix()}"
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)

    # Schema up to the revision BEFORE check_versions, then seed a legacy check.
    command.upgrade(cfg, "0010_connection_grants")
    engine = create_engine(url)
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO checks (id, dataset_id, name, check_type, column_name, params, "
            "severity, status, origin, rationale, schedule_kind, schedule_expr, created_at) "
            "VALUES (1, 1, 'legacy nn', 'not_null', 'email', '{}', 'error', 'active', "
            "'manual', '', 'interval', '1440', '2026-01-01 00:00:00')"
        )

    # Applying 0011 backfills a v1 baseline carrying the check's definition.
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        rows = c.exec_driver_sql(
            "SELECT check_id, version, change_note, check_type, column_name FROM check_versions"
        ).fetchall()
    assert rows == [(1, 1, "baseline", "not_null", "email")]
    engine.dispose()


def test_existing_create_all_db_is_stamped(monkeypatch):
    """A pre-Alembic DB (built by create_all, no alembic_version) is adopted via
    `stamp head` — NOT re-migrated (which would fail on the already-present tables).
    """
    from app import db as appdb

    tmp = Path(tempfile.mkdtemp(prefix="dq-stamp-"))
    url = f"sqlite:///{(tmp / 'legacy.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)  # simulate a deployment created before #23
    assert inspect(engine).has_table("users")
    assert not inspect(engine).has_table("alembic_version")

    # Point the migration helper's URL at the legacy DB without touching globals.
    class _S:
        database_url = url

    monkeypatch.setattr(appdb, "get_settings", lambda: _S())

    appdb._run_migrations(engine)

    insp = inspect(engine)
    assert insp.has_table("alembic_version")
    with engine.connect() as c:
        version = c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    # Stamped at head (not re-migrated): equals the latest revision, whatever it is.
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert version == head
    engine.dispose()
