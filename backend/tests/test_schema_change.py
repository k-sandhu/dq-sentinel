"""Schema-change check (#101): run-over-run + pinned baseline detection, end to end.

Builds its own writable sqlite source so the table schema can be mutated between
runs (the shared `source_db` fixture must stay immutable for other tests).
"""

import sqlite3
import tempfile
import uuid
from pathlib import Path

from app.core.runner import run_check
from app.db import init_db, session_factory
from app.models import Check, Connection, Dataset, ExceptionRecord, SchemaSnapshot


def _source(cols_sql: str) -> tuple[str, Path]:
    d = Path(tempfile.mkdtemp(prefix="dq-schemachg-"))
    path = d / "src.sqlite"
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE t ({cols_sql})")
    con.commit()
    con.close()
    return f"sqlite:///{path.as_posix()}", path


def _recreate(path: Path, cols_sql: str) -> None:
    con = sqlite3.connect(path)
    con.execute("DROP TABLE IF EXISTS t")
    con.execute(f"CREATE TABLE t ({cols_sql})")
    con.commit()
    con.close()


def _setup(db, dsn: str, params: dict) -> int:
    conn = Connection(name=f"sc-{uuid.uuid4().hex}", kind="sqlite", dsn=dsn)
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, table_name="t", display_name="t")
    db.add(ds)
    db.flush()
    check = Check(
        dataset_id=ds.id,
        name="schema check",
        check_type="schema_change",
        severity="error",
        status="active",
        params=params,
    )
    db.add(check)
    db.commit()
    db.refresh(check)
    return check.id


def test_previous_baseline_detects_then_rebaselines():
    init_db()
    dsn, path = _source("id INTEGER, email TEXT, age INTEGER")
    factory = session_factory()
    with factory() as db:
        check_id = _setup(db, dsn, {"baseline": "previous", "on_added": True})
        run1 = run_check(db, db.get(Check, check_id), triggered_by="manual")
        assert run1.status == "pass"
        assert run1.metrics.get("note") == "baseline captured"

    _recreate(path, "id INTEGER, age TEXT, city TEXT")  # -email, age INT->TEXT, +city
    with factory() as db:
        run2 = run_check(db, db.get(Check, check_id), triggered_by="schedule")
        assert run2.status == "fail"
        assert run2.violation_count == 3
        recs = db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).all()
        assert {r.row_data["change_type"] for r in recs} == {"removed", "type_changed", "added"}
        assert set(run2.metrics["removed"]) == {"email"}
        assert set(run2.metrics["added"]) == {"city"}

    # No further change: the 'previous' baseline advanced to run2's schema -> pass,
    # and the change alerts stay OPEN (rows_evaluated is None -> no auto-resolve).
    with factory() as db:
        run3 = run_check(db, db.get(Check, check_id), triggered_by="schedule")
        assert run3.status == "pass"
        assert run3.violation_count == 0
        open_n = (
            db.query(ExceptionRecord)
            .filter(ExceptionRecord.check_id == check_id, ExceptionRecord.status == "open")
            .count()
        )
        assert open_n == 3


def test_pinned_baseline_keeps_alerting():
    init_db()
    dsn, path = _source("id INTEGER, name TEXT")
    factory = session_factory()
    with factory() as db:
        check_id = _setup(db, dsn, {"baseline": "pinned"})
        ds_id = db.get(Check, check_id).dataset_id
        run1 = run_check(db, db.get(Check, check_id))
        assert run1.status == "pass"  # first run pins the baseline
        assert (
            db.query(SchemaSnapshot)
            .filter(SchemaSnapshot.dataset_id == ds_id, SchemaSnapshot.is_baseline.is_(True))
            .count()
            == 1
        )

    _recreate(path, "id INTEGER")  # remove 'name'
    with factory() as db:
        run2 = run_check(db, db.get(Check, check_id))
        assert run2.violation_count == 1
        assert run2.metrics["removed"] == ["name"]

    # Pinned baseline does NOT advance -> the breach persists on the next run too.
    with factory() as db:
        run3 = run_check(db, db.get(Check, check_id))
        assert run3.violation_count == 1
        assert run3.metrics["removed"] == ["name"]


def test_ignore_columns_suppresses_change():
    init_db()
    dsn, path = _source("id INTEGER, name TEXT")
    factory = session_factory()
    with factory() as db:
        check_id = _setup(db, dsn, {"baseline": "previous", "ignore_columns": ["name"]})
        run_check(db, db.get(Check, check_id))  # baseline (name ignored)
    _recreate(path, "id INTEGER")  # remove the ignored 'name'
    with factory() as db:
        run2 = run_check(db, db.get(Check, check_id))
        assert run2.violation_count == 0  # change in an ignored column is invisible


def test_schema_history_and_pin_baseline_api(client, admin_headers, source_db):
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"schemahist-{uuid.uuid4().hex}", "dsn": source_db},
        headers=admin_headers,
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=admin_headers,
    ).json()[0]
    ds_id = ds["id"]

    # empty before any profiling
    hist = client.get(f"/api/v1/datasets/{ds_id}/schema-history", headers=admin_headers).json()
    assert hist["snapshots"] == [] and hist["pinned_baseline_id"] is None

    # profiling captures a (deduped) snapshot
    assert client.post(f"/api/v1/datasets/{ds_id}/profile", headers=admin_headers).status_code == 200
    hist = client.get(f"/api/v1/datasets/{ds_id}/schema-history", headers=admin_headers).json()
    assert len(hist["snapshots"]) == 1
    snap = hist["snapshots"][0]
    assert snap["source"] == "profile"
    assert {c["name"] for c in snap["columns"]} == {"id", "email", "age", "status", "score", "created_at"}

    # pin the current schema as baseline
    base = client.post(f"/api/v1/datasets/{ds_id}/schema-baseline", headers=admin_headers).json()
    assert base["is_baseline"] is True
    hist = client.get(f"/api/v1/datasets/{ds_id}/schema-history", headers=admin_headers).json()
    assert hist["pinned_baseline_id"] == base["id"]


def test_schema_baseline_requires_editor(client, source_db):
    # unauthenticated -> 401 (mutation endpoint)
    assert client.post("/api/v1/datasets/1/schema-baseline").status_code == 401
