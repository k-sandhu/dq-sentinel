"""Daily operating console (#64): /dashboard/console work-queue counters."""

from datetime import timedelta

import pytest

from app.db import session_factory
from app.models import ExceptionRecord, utcnow

API = "/api/v1/dashboard/console"


@pytest.fixture(scope="module")
def seeded(client, admin_headers, source_db):
    h = admin_headers
    conn = client.post(
        "/api/v1/connections", json={"name": "console-src", "dsn": source_db}, headers=h
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]
    c_err = client.post(
        "/api/v1/checks",
        json={"dataset_id": ds["id"], "check_type": "not_null", "column_name": "email",
              "severity": "error", "name": "console err"},
        headers=h,
    ).json()
    run = client.post(f"/api/v1/checks/{c_err['id']}/run", headers=h).json()  # 5 exceptions, check fails
    me = client.get("/api/v1/auth/me", headers=h).json()

    now = utcnow()
    factory = session_factory()
    with factory() as db:
        recs = (
            db.query(ExceptionRecord)
            .filter(ExceptionRecord.check_id == c_err["id"])
            .order_by(ExceptionRecord.id)
            .all()
        )
        # recs[0]: assigned to me, regressed (recurred after triage)
        recs[0].assigned_to_id = me["id"]
        recs[0].occurrence_count = 3
        recs[0].marked_at = now - timedelta(hours=1)
        # recs[1]: resolved in the last 24h
        recs[1].status = "resolved"
        recs[1].marked_at = now - timedelta(hours=2)
        # recs[2]: first seen long ago (not "new in 24h") and still open
        recs[2].first_seen_at = now - timedelta(days=10)
        db.commit()

    return {"h": h, "dataset_id": ds["id"], "check_err": c_err["id"], "run": run["id"], "me": me["id"]}


def test_console_counters(client, seeded):
    body = client.get(API, headers=seeded["h"]).json()
    # 5 exceptions; recs[2] backdated -> 4 new in the last 24h.
    assert body["new_exceptions_24h"] >= 4
    assert body["resolved_24h"] >= 1
    assert body["regressed_open"] >= 1  # recs[0]: open, occurrence>1, marked
    assert body["assigned_to_me_open"] >= 1
    assert body["open_total"] >= 1


def test_console_failing_now_and_movers(client, seeded):
    body = client.get(API, headers=seeded["h"]).json()

    # The error check that just failed appears in failing_now (cap 8).
    names = [c["name"] for c in body["failing_now"]]
    assert "console err" in names
    assert len(body["failing_now"]) <= 8

    # Our dataset is among the movers, with opened/open counts.
    movers = {m["dataset_id"]: m for m in body["movers"]}
    assert seeded["dataset_id"] in movers
    mine = movers[seeded["dataset_id"]]
    assert mine["opened_24h"] >= 4
    assert mine["open_total"] >= 1
    assert mine["dataset_name"]
    assert len(body["movers"]) <= 5


def test_console_requires_auth(client):
    assert client.get(API).status_code == 401
