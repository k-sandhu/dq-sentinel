"""Global search endpoint (issue #43): GET /search across datasets, checks,
connections, and (soft) saved queries.

Seeds one connection + dataset + check with distinctive names so assertions are
unaffected by data other session-scoped tests leave in the shared app DB. The
app DB is shared and session-scoped, so seeding happens once (module fixtures)
to avoid tripping the unique constraints on connection/dataset names.
"""

import pytest

from app.db import init_db, session_factory
from app.models import Check, Connection, Dataset


@pytest.fixture(scope="module")
def seeded(client) -> dict[str, int]:
    init_db()
    with session_factory()() as db:
        conn = Connection(name="ZephyrWarehouse", kind="sqlite", dsn="sqlite:///:memory:")
        db.add(conn)
        db.flush()
        ds = Dataset(
            connection_id=conn.id,
            schema_name="zephyr",
            table_name="zephyr_orders",
            display_name="Zephyr Orders",
        )
        db.add(ds)
        db.flush()
        chk = Check(
            dataset_id=ds.id,
            name="ZephyrFreshness window",
            check_type="freshness",
            column_name="created_at",
            status="active",
        )
        db.add(chk)
        db.commit()
        return {"conn": conn.id, "dataset": ds.id, "check": chk.id}


def test_search_requires_auth(client):
    assert client.get("/api/v1/search?q=zephyr").status_code == 401


def test_search_blank_query_returns_empty(client, admin_headers):
    resp = client.get("/api/v1/search?q=", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == {"hits": []}


def test_search_matches_dataset(client, admin_headers, seeded):
    resp = client.get("/api/v1/search?q=zephyr_orders", headers=admin_headers)
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    ds_hits = [h for h in hits if h["type"] == "dataset" and h["id"] == seeded["dataset"]]
    assert len(ds_hits) == 1
    hit = ds_hits[0]
    assert hit["title"] == "zephyr.zephyr_orders"  # schema-qualified
    assert hit["subtitle"] == "ZephyrWarehouse"  # connection name
    assert hit["url"] == f"/datasets/{seeded['dataset']}"


def test_search_matches_check_to_dataset_tab(client, admin_headers, seeded):
    resp = client.get("/api/v1/search?q=ZephyrFreshness", headers=admin_headers)
    hits = resp.json()["hits"]
    chk_hits = [h for h in hits if h["type"] == "check"]
    assert len(chk_hits) == 1
    hit = chk_hits[0]
    assert hit["id"] == seeded["check"]
    assert hit["title"] == "ZephyrFreshness window"
    assert hit["subtitle"] == "zephyr_orders"  # dataset table name
    assert hit["url"] == f"/datasets/{seeded['dataset']}/checks"  # Checks tab


def test_search_matches_connection(client, admin_headers, seeded):
    resp = client.get("/api/v1/search?q=ZephyrWarehouse", headers=admin_headers)
    hits = resp.json()["hits"]
    conn_hits = [h for h in hits if h["type"] == "connection"]
    assert len(conn_hits) == 1
    hit = conn_hits[0]
    assert hit["id"] == seeded["conn"]
    assert hit["title"] == "ZephyrWarehouse"
    assert hit["subtitle"] == "sqlite"  # kind
    assert hit["url"] == "/connections"


def test_search_is_case_insensitive(client, admin_headers, seeded):
    lower = client.get("/api/v1/search?q=zephyrwarehouse", headers=admin_headers).json()["hits"]
    upper = client.get("/api/v1/search?q=ZEPHYRWAREHOUSE", headers=admin_headers).json()["hits"]
    assert any(h["type"] == "connection" for h in lower)
    assert any(h["type"] == "connection" for h in upper)


def test_search_saved_queries_absent_is_silent(client, admin_headers, seeded):
    """#41's saved_queries table is absent in this worktree — endpoint must not 500."""
    resp = client.get("/api/v1/search?q=zephyr", headers=admin_headers)
    assert resp.status_code == 200
    assert all(h["type"] != "saved_query" for h in resp.json()["hits"])


def test_search_limit_respected(client, admin_headers):
    """limit caps each entity type independently."""
    init_db()
    with session_factory()() as db:
        conn = Connection(name="LimitProbe", kind="sqlite", dsn="sqlite:///:memory:")
        db.add(conn)
        db.flush()
        for i in range(5):
            db.add(
                Dataset(
                    connection_id=conn.id,
                    table_name=f"limitprobe_tbl_{i}",
                    display_name=f"limitprobe {i}",
                )
            )
        db.commit()

    hits = client.get("/api/v1/search?q=limitprobe&limit=2", headers=admin_headers).json()["hits"]
    dataset_hits = [h for h in hits if h["type"] == "dataset"]
    assert len(dataset_hits) == 2  # capped at limit even though 5 match


def test_search_orders_exact_prefix_first(client, admin_headers):
    """A dataset whose name starts with the query outranks a non-prefix match."""
    init_db()
    with session_factory()() as db:
        conn = Connection(name="PrefixProbe", kind="sqlite", dsn="sqlite:///:memory:")
        db.add(conn)
        db.flush()
        # "xx_prefixhit" contains but does not start with the needle;
        # "prefixhit_orders" starts with it.
        db.add(Dataset(connection_id=conn.id, table_name="xx_prefixhit", display_name="xx"))
        db.add(Dataset(connection_id=conn.id, table_name="prefixhit_orders", display_name="ph"))
        db.commit()

    hits = client.get("/api/v1/search?q=prefixhit&limit=5", headers=admin_headers).json()["hits"]
    ds_titles = [h["title"] for h in hits if h["type"] == "dataset"]
    assert ds_titles.index("prefixhit_orders") < ds_titles.index("xx_prefixhit")
