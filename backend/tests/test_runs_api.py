from datetime import timedelta
from uuid import uuid4

from app.db import session_factory
from app.models import CheckRun, utcnow
from app.schemas import MAX_OFFSET


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


def test_runs_support_dashboard_drilldown_filters(client, admin_headers, source_db):
    dataset_id, check_id = _seed_check(client, admin_headers, source_db)
    now = utcnow()
    target_day = now - timedelta(days=2)
    recent = now - timedelta(hours=2)
    old = now - timedelta(days=10)

    with session_factory()() as db:
        db.add_all(
            [
                CheckRun(
                    check_id=check_id,
                    dataset_id=dataset_id,
                    started_at=target_day,
                    status="fail",
                    violation_count=3,
                    triggered_by="manual",
                ),
                CheckRun(
                    check_id=check_id,
                    dataset_id=dataset_id,
                    started_at=recent,
                    status="pass",
                    violation_count=0,
                    triggered_by="schedule",
                ),
                CheckRun(
                    check_id=check_id,
                    dataset_id=dataset_id,
                    started_at=old,
                    status="error",
                    violation_count=0,
                    triggered_by="schedule",
                ),
            ]
        )
        db.commit()

    day = target_day.strftime("%Y-%m-%d")
    by_day = client.get(
        f"/api/v1/runs?check_id={check_id}&day={day}&status=fail", headers=admin_headers
    )
    assert by_day.status_code == 200
    assert [r["status"] for r in by_day.json()] == ["fail"]

    recent_runs = client.get(
        f"/api/v1/runs?check_id={check_id}&since=24h", headers=admin_headers
    )
    assert recent_runs.status_code == 200
    assert [r["status"] for r in recent_runs.json()] == ["pass"]

    bad_day = client.get(f"/api/v1/runs?check_id={check_id}&day=06-13", headers=admin_headers)
    assert bad_day.status_code == 422

    bad_since = client.get(
        f"/api/v1/runs?check_id={check_id}&since=90d", headers=admin_headers
    )
    assert bad_since.status_code == 422


def test_pagination_bounds(client, admin_headers, source_db):
    """#274: /runs had the same raw-offset pattern as /exceptions, plus a
    `min(limit, 200)` that let a negative limit through — and SQLite reads
    `LIMIT -1` as *unbounded*, so `?limit=-1` dumped every visible run.
    """
    h = admin_headers
    dataset_id, check_id = _seed_check(client, h, source_db)
    with session_factory()() as db:
        db.add_all(
            [
                CheckRun(
                    check_id=check_id,
                    dataset_id=dataset_id,
                    started_at=utcnow() - timedelta(minutes=i),
                    status="pass",
                    violation_count=0,
                    triggered_by="manual",
                )
                for i in range(3)
            ]
        )
        db.commit()

    assert client.get("/api/v1/runs?offset=-1", headers=h).status_code == 422
    assert client.get(f"/api/v1/runs?offset={MAX_OFFSET + 1}", headers=h).status_code == 422

    page = client.get(f"/api/v1/runs?check_id={check_id}&limit=2&offset=0", headers=h)
    assert page.status_code == 200 and len(page.json()) == 2
    assert page.headers["X-Total-Count"] == "3"
    # An out-of-range page is an empty list, not an error.
    past = client.get(f"/api/v1/runs?check_id={check_id}&offset=50", headers=h)
    assert past.status_code == 200 and past.json() == []
    edge = client.get(f"/api/v1/runs?check_id={check_id}&offset={MAX_OFFSET}", headers=h)
    assert edge.status_code == 200 and edge.json() == []

    # A negative limit must not mean "no limit"; the 200 cap is unchanged.
    assert len(client.get(f"/api/v1/runs?check_id={check_id}&limit=-1", headers=h).json()) == 1
    assert client.get(f"/api/v1/runs?check_id={check_id}&limit=500", headers=h).status_code == 200
