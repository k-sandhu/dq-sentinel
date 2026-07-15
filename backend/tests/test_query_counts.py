"""Query-count regression guards for the hot list endpoints (perf).

The /runs list used to issue one exceptions COUNT per row plus lazy check/
dataset loads — a 100-row page cost ~300 queries. These tests pin the per-page
query budget so an accidental N+1 reintroduction fails loudly. Budgets are
deliberately loose (auth + grant scoping add a few statements per request);
the point is the order of magnitude, not the exact number.
"""

from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine


@contextmanager
def count_statements():
    counter = {"n": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(Engine, "before_cursor_execute", _before)
    try:
        yield counter
    finally:
        event.remove(Engine, "before_cursor_execute", _before)


@pytest.fixture(scope="module")
def perf_seeded(client, admin_headers, source_db):
    """One dataset with a failing check run a dozen times — enough rows that an
    N+1 would blow well past the budgets below."""
    h = admin_headers
    conn = client.post(
        "/api/v1/connections", json={"name": "perfcount-src", "dsn": source_db}, headers=h
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]
    check = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds["id"], "check_type": "not_null", "column_name": "email",
            "severity": "error", "name": "perfcount email not null",
        },
        headers=h,
    ).json()
    for _ in range(12):
        r = client.post(f"/api/v1/checks/{check['id']}/run", headers=h)
        assert r.status_code == 200, r.text
    return {"h": h, "dataset_id": ds["id"], "check_id": check["id"]}


def test_runs_list_query_budget(client, perf_seeded):
    h = perf_seeded["h"]
    with count_statements() as counter:
        resp = client.get("/api/v1/runs?limit=50", headers=h)
    assert resp.status_code == 200
    assert len(resp.json()) >= 12
    # Pre-batching this page cost ~3 queries per run row; the budget holds the
    # whole request (auth + grants + count + page + joined loads + one GROUP BY)
    # to a small constant.
    assert counter["n"] <= 10, f"/runs issued {counter['n']} queries for one page"


def test_datasets_list_query_budget(client, perf_seeded):
    h = perf_seeded["h"]
    with count_statements() as counter:
        resp = client.get("/api/v1/datasets", headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert any(d["id"] == perf_seeded["dataset_id"] for d in body)
    # selectinload fans out one statement per relationship (checks/knowledge/
    # connection) regardless of row count, plus one GROUP BY for open counts.
    assert counter["n"] <= 12, f"/datasets issued {counter['n']} queries for one page"


def test_exceptions_list_query_budget(client, perf_seeded):
    h = perf_seeded["h"]
    with count_statements() as counter:
        resp = client.get(
            f"/api/v1/exceptions?dataset_id={perf_seeded['dataset_id']}&limit=50", headers=h
        )
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
    # ~15 statements today (auth + grants + count + page + 3 warm batches +
    # session bookkeeping) for a 50-row page; an N+1 would be 60+.
    assert counter["n"] <= 18, f"/exceptions issued {counter['n']} queries for one page"


def test_check_detail_endpoint(client, perf_seeded):
    """The new single-check fetch the detail page uses instead of the full list."""
    h = perf_seeded["h"]
    one = client.get(f"/api/v1/checks/{perf_seeded['check_id']}", headers=h)
    assert one.status_code == 200
    assert one.json()["id"] == perf_seeded["check_id"]
    assert one.json()["dataset_name"]
    missing = client.get("/api/v1/checks/99999999", headers=h)
    assert missing.status_code == 404
