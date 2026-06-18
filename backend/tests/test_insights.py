"""Insights API (#69): check-matrix + exception-series curated aggregates.

Matrix runs are seeded directly (controlled started_at/status) so worst-of-day
precedence and no-run days are deterministic; series exceptions are created for
real, then their first_seen_at is spread across days.
"""

from datetime import timedelta

import pytest

from app.db import session_factory
from app.models import CheckRun, ExceptionRecord, utcnow

API = "/api/v1/insights"


def _day(now, n: int) -> str:
    return (now - timedelta(days=n)).strftime("%Y-%m-%d")


@pytest.fixture(scope="module")
def seed(client, admin_headers, source_db):
    h = admin_headers
    conn = client.post(
        "/api/v1/connections", json={"name": "insights-src", "dsn": source_db}, headers=h
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]

    # Matrix checks — never executed; runs are seeded by hand below.
    cm_err = client.post(
        "/api/v1/checks",
        json={"dataset_id": ds["id"], "check_type": "not_null", "column_name": "email",
              "severity": "error", "name": "ins matrix err"},
        headers=h,
    ).json()
    cm_warn = client.post(
        "/api/v1/checks",
        json={"dataset_id": ds["id"], "check_type": "not_null", "column_name": "status",
              "severity": "warn", "name": "ins matrix warn"},
        headers=h,
    ).json()

    # Series checks — executed to produce real exceptions (5 null emails, 1 bad status).
    cs_err = client.post(
        "/api/v1/checks",
        json={"dataset_id": ds["id"], "check_type": "not_null", "column_name": "email",
              "severity": "error", "name": "ins series err"},
        headers=h,
    ).json()
    cs_warn = client.post(
        "/api/v1/checks",
        json={"dataset_id": ds["id"], "check_type": "accepted_values", "column_name": "status",
              "params": {"values": ["active", "inactive"]}, "severity": "warn",
              "name": "ins series warn"},
        headers=h,
    ).json()
    client.post(f"/api/v1/checks/{cs_err['id']}/run", headers=h)
    client.post(f"/api/v1/checks/{cs_warn['id']}/run", headers=h)

    now = utcnow()
    factory = session_factory()
    with factory() as db:
        def run(check_id: int, n: int, status: str):
            db.add(CheckRun(check_id=check_id, dataset_id=ds["id"],
                            started_at=now - timedelta(days=n), status=status))

        # cm_err: today pass+fail (worst=fail, 2 runs); 1d warn; 2d pass; 3d error+pass (worst=error)
        run(cm_err["id"], 0, "pass")
        run(cm_err["id"], 0, "fail")
        run(cm_err["id"], 1, "warn")
        run(cm_err["id"], 2, "pass")
        run(cm_err["id"], 3, "error")
        run(cm_err["id"], 3, "pass")
        run(cm_err["id"], 10, "fail")  # outside a 7-day window — must be excluded
        # cm_warn: a single pass today
        run(cm_warn["id"], 0, "pass")

        # Spread the 5 cs_err exceptions: 2 today, 1@1d, 1@2d, 1@100d (out of 30d window).
        err_recs = (
            db.query(ExceptionRecord)
            .filter(ExceptionRecord.check_id == cs_err["id"])
            .order_by(ExceptionRecord.id)
            .all()
        )
        for rec, n in zip(err_recs, [0, 0, 1, 2, 100], strict=True):
            rec.first_seen_at = now - timedelta(days=n)
        # cs_warn exception: today
        for rec in db.query(ExceptionRecord).filter(ExceptionRecord.check_id == cs_warn["id"]).all():
            rec.first_seen_at = now
        db.commit()

    return {
        "h": h, "now": now, "dataset_id": ds["id"],
        "cm_err": cm_err["id"], "cm_warn": cm_warn["id"],
        "cs_err": cs_err["id"], "cs_warn": cs_warn["id"],
    }


# ---- check-matrix -----------------------------------------------------------
def test_matrix_columns_and_worst_of_day(client, seed):
    now = seed["now"]
    resp = client.get(
        f"{API}/check-matrix",
        params={"check_ids": [seed["cm_err"], seed["cm_warn"], 99_999_999], "days": 7},
        headers=seed["h"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 7 UTC-day columns, oldest -> newest, last == today.
    assert len(body["columns"]) == 7
    assert body["columns"] == sorted(body["columns"])
    assert body["columns"][-1] == _day(now, 0)

    # Unknown id 99999999 is skipped silently — only the two real checks return.
    rows = {r["check_id"]: r for r in body["rows"]}
    assert set(rows) == {seed["cm_err"], seed["cm_warn"]}

    err = rows[seed["cm_err"]]
    assert err["check_name"] == "ins matrix err"
    assert err["connection_name"] == "insights-src"
    assert err["severity"] == "error"
    assert err["dataset_name"]  # resolved (display name or schema.table)
    assert err["dataset_id"] == seed["dataset_id"]  # drives the row's dataset link

    cols = body["columns"]

    def cell(row, n):
        return row["cells"][cols.index(_day(now, n))]

    assert cell(err, 0) == {"status": "fail", "runs": 2}  # pass+fail -> fail
    assert cell(err, 1) == {"status": "warn", "runs": 1}
    assert cell(err, 2) == {"status": "pass", "runs": 1}
    assert cell(err, 3) == {"status": "error", "runs": 2}  # error+pass -> error
    # A day with no runs is None / 0.
    assert cell(err, 4) == {"status": None, "runs": 0}
    assert cell(err, 5) == {"status": None, "runs": 0}
    # The 10-day-old run is outside the 7-day window entirely.
    assert sum(c["runs"] for c in err["cells"]) == 6

    warn = rows[seed["cm_warn"]]
    assert cell(warn, 0) == {"status": "pass", "runs": 1}


def test_matrix_caps_and_validation(client, seed):
    h = seed["h"]
    too_many = client.get(
        f"{API}/check-matrix", params={"check_ids": list(range(1, 27)), "days": 14}, headers=h
    )
    assert too_many.status_code == 422
    assert "25" in too_many.json()["detail"]

    bad_days = client.get(
        f"{API}/check-matrix", params={"check_ids": [seed["cm_err"]], "days": 5}, headers=h
    )
    assert bad_days.status_code == 422

    empty = client.get(f"{API}/check-matrix", params={"days": 7}, headers=h)
    assert empty.status_code == 200
    assert empty.json()["rows"] == []
    assert len(empty.json()["columns"]) == 7


# ---- exception-series -------------------------------------------------------
def test_series_buckets_and_total(client, seed):
    now = seed["now"]
    resp = client.get(
        f"{API}/exception-series",
        params={"dataset_id": seed["dataset_id"], "days": 30},
        headers=seed["h"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_day = {p["t"]: p["value"] for p in body["points"]}
    assert len(body["points"]) == 30
    assert by_day[_day(now, 0)] == 3  # 2 err + 1 warn today
    assert by_day[_day(now, 1)] == 1
    assert by_day[_day(now, 2)] == 1
    # The 100-day-old exception is outside the window.
    assert body["total"] == 5


def test_series_honors_filters(client, seed):
    now = seed["now"]
    only_err = client.get(
        f"{API}/exception-series",
        params={"dataset_id": seed["dataset_id"], "severity": ["error"], "days": 30},
        headers=seed["h"],
    ).json()
    assert only_err["total"] == 4  # 5 err minus the out-of-window one
    by_day = {p["t"]: p["value"] for p in only_err["points"]}
    assert by_day[_day(now, 0)] == 2

    only_warn = client.get(
        f"{API}/exception-series",
        params={"dataset_id": seed["dataset_id"], "severity": ["warn"], "days": 30},
        headers=seed["h"],
    ).json()
    assert only_warn["total"] == 1


def test_series_validation(client, seed):
    h = seed["h"]
    assert client.get(f"{API}/exception-series", params={"days": 91}, headers=h).status_code == 422
    assert client.get(f"{API}/exception-series", params={"days": 0}, headers=h).status_code == 422
    assert (
        client.get(f"{API}/exception-series", params={"days": 30, "interval": "hour"}, headers=h)
        .status_code
        == 422
    )


def test_insights_require_auth(client):
    assert client.get(f"{API}/check-matrix", params={"days": 7}).status_code == 401
    assert client.get(f"{API}/exception-series", params={"days": 30}).status_code == 401
