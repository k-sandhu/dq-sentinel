"""Migration 0007's downgrade narrows a populated column — guard it (#314).

``0007_incidents`` widens ``notification_rules.channel`` from ``VARCHAR(10)`` to
``VARCHAR(20)``; its ``downgrade()`` narrows it back. Every channel name this
release can write still fits (``servicenow``, 10 chars, is the longest), but
nothing at the DB level enforces that — so a longer value written by a later
release or by hand turns the downgrade into an opaque mid-migration driver error
on PostgreSQL, or a silent no-op on SQLite, which ignores VARCHAR lengths.

These tests run on SQLite (like the rest of the suite), which is exactly why the
pre-flight check has to be in Python: SQLite would happily "downgrade" a 24-char
value into a ``VARCHAR(10)`` column and leave the data mismatched with the
declared schema. The complement is the ``migrations-postgres`` CI job.
"""

import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import BACKEND_DIR

BEFORE = "0006_dataset_monitor_packs"
TARGET = "0007_incidents"


def _at_0007(prefix: str):
    """A fresh SQLite DB migrated to exactly 0007, plus its alembic Config."""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    url = f"sqlite:///{(tmp / 'mig0007.db').as_posix()}"
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, TARGET)
    return cfg, create_engine(url)


def _insert_rule(engine, channel: str) -> None:
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO notification_rules "
            "(id, dataset_id, min_severity, channel, target, on_error_runs, enabled, "
            " created_at, dedupe_window_minutes, max_escalation_level) "
            f"VALUES (1, NULL, 'error', '{channel}', '', 1, 1, "
            "'2026-01-01 00:00:00', 60, 0)"
        )


def test_downgrade_refuses_channel_too_wide_for_narrowed_column():
    """A stored channel longer than VARCHAR(10) must abort the downgrade with a
    message naming the row — not truncate it, and not (as before this guard)
    "succeed" on SQLite while leaving data the declared type cannot hold."""
    cfg, engine = _at_0007("dq-0007-wide-")
    _insert_rule(engine, "a-very-long-channel-name")  # 24 chars

    with pytest.raises(RuntimeError) as excinfo:
        command.downgrade(cfg, BEFORE)

    msg = str(excinfo.value)
    assert "0007_incidents" in msg
    assert "id=1" in msg
    assert "a-very-long-channel-name" in msg

    # Refused BEFORE any DDL ran: the widened schema is intact, so the operator
    # can fix the data and retry rather than restore a half-downgraded DB.
    cols = {c["name"] for c in inspect(engine).get_columns("notification_rules")}
    assert {"dedupe_window_minutes", "escalation_delay_minutes", "max_escalation_level"} <= cols
    engine.dispose()


def test_downgrade_succeeds_for_the_longest_supported_channel():
    """`servicenow` is the longest channel schemas.NotifyChannel allows (exactly
    10 chars), so real data downgrades cleanly and the guard stays out of the way."""
    cfg, engine = _at_0007("dq-0007-ok-")
    _insert_rule(engine, "servicenow")

    command.downgrade(cfg, BEFORE)

    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("notification_rules")}
    assert "dedupe_window_minutes" not in cols
    assert "max_escalation_level" not in cols
    assert cols["channel"]["type"].length == 10
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT channel FROM notification_rules").scalar() == "servicenow"
    assert not insp.has_table("incidents")
    engine.dispose()
