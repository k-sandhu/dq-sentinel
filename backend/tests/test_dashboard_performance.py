from datetime import timedelta
from uuid import uuid4

from app.db import session_factory
from app.models import CheckRun, ExceptionRecord


def _seed_check(client, admin_headers, source_db):
    suffix = uuid4().hex
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"runs-filter-{suffix}", "dsn": source_db},
        headers=admin_headers,
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=admin_headers,
    ).json()[0]
    check = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds["id"],
            "check_type": "not_null",
            "column_name": "email",
            "name": f"runs filter {suffix}",
        },
        headers=admin_headers,
    ).json()
    return ds["id"], check["id"]


def test_dashboard_trend_aggregates_history_in_sql(client, admin_headers, source_db, monkeypatch):
    from datetime import datetime

    from sqlalchemy import event

    from app.api import dashboard

    now = datetime(2020, 1, 15, 12)
    monkeypatch.setattr(dashboard, "utcnow", lambda: now)
    dataset_id, check_id = _seed_check(client, admin_headers, source_db)
    with session_factory()() as db:
        runs = [
            CheckRun(
                check_id=check_id, dataset_id=dataset_id, started_at=now - timedelta(days=1), status="pass"
            )
            for _ in range(60)
        ]
        runs += [
            CheckRun(check_id=check_id, dataset_id=dataset_id, started_at=now, status="warn")
            for _ in range(10)
        ]
        runs += [
            CheckRun(check_id=check_id, dataset_id=dataset_id, started_at=now, status="fail")
            for _ in range(5)
        ]
        runs += [
            CheckRun(
                check_id=check_id, dataset_id=dataset_id, started_at=now + timedelta(hours=1), status="error"
            )
        ]
        db.add_all(runs)
        db.commit()
        engine = db.get_bind()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        resp = client.get("/api/v1/dashboard", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        trend = {r["day"]: r for r in resp.json()["trend"]}
        assert len(trend) == 14
        assert trend["2020-01-14"]["passed"] == 60
        assert trend["2020-01-15"]["warned"] == 10
        assert trend["2020-01-15"]["failed"] == 5
        assert trend["2020-01-15"]["errored"] == 0
        assert any("group by date(check_runs.started_at), check_runs.status" in s for s in statements)
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_recent_run_serialization_has_constant_query_count(client, admin_headers, source_db):
    from sqlalchemy import event

    from app.api.serialize import runs_out

    dataset_id, check_id = _seed_check(client, admin_headers, source_db)
    with session_factory()() as db:
        runs = [CheckRun(check_id=check_id, dataset_id=dataset_id, status="fail") for _ in range(20)]
        db.add_all(runs)
        db.flush()
        ids = [run.id for run in runs]
        db.add_all(
            [
                ExceptionRecord(run_id=run.id, check_id=check_id, dataset_id=dataset_id)
                for i, run in enumerate(runs)
                for _ in range(i % 3)
            ]
        )
        db.commit()

    for size in (1, 20):
        with session_factory()() as db:
            # Load inputs first, then measure only serialization in a fresh session.
            page = db.query(CheckRun).filter(CheckRun.id.in_(ids[:size])).order_by(CheckRun.id.desc()).all()
            engine = db.get_bind()
            statements = []

            def capture(conn, cursor, statement, parameters, context, executemany, bucket=statements):
                bucket.append(statement)

            event.listen(engine, "before_cursor_execute", capture)
            try:
                result = runs_out(db, page)
                assert [run.id for run in result] == list(reversed(ids[:size]))
                assert [run.exception_count for run in result] == list(reversed([i % 3 for i in range(size)]))
                assert all(run.dataset_name == "people" and run.check_type == "not_null" for run in result)
                assert len(statements) == 2
            finally:
                event.remove(engine, "before_cursor_execute", capture)
