from datetime import timedelta
from uuid import uuid4

from app.db import session_factory
from app.models import CheckRun, utcnow


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
