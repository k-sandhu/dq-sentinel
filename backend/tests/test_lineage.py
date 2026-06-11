"""DDL + lineage endpoints (issue #51): sqlglot table-ref extraction, graph
building from view definitions, depth-bounded subgraphs, and the check-health
overlay. Uses its own tmp sqlite source DB (tables + views, one with a CTE)."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.core.lineage import extract_table_refs, sqlglot_dialect

# ---- unit: dialect mapping ----


def test_sqlglot_dialect_mapping():
    assert sqlglot_dialect("sqlite") == "sqlite"
    assert sqlglot_dialect("duckdb") == "duckdb"
    assert sqlglot_dialect("postgresql") == "postgres"
    assert sqlglot_dialect("mssql") == "tsql"
    assert sqlglot_dialect("bigquery") == "bigquery"
    assert sqlglot_dialect("no-such-engine") is None


# ---- unit: extract_table_refs ----


def test_extract_refs_basic_join():
    refs = extract_table_refs(
        "SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id", "sqlite"
    )
    assert refs == {(None, "orders"), (None, "customers")}


def test_extract_refs_quoted_and_schema_qualified():
    refs = extract_table_refs(
        'SELECT * FROM "Orders" JOIN analytics."Customers" ON 1 = 1', "postgres"
    )
    assert refs == {(None, "orders"), ("analytics", "customers")}


def test_extract_refs_excludes_cte_aliases():
    refs = extract_table_refs(
        "WITH last7 AS (SELECT * FROM orders WHERE created_at >= '2026-06-01') "
        "SELECT * FROM last7 JOIN customers ON customers.id = last7.customer_id",
        "sqlite",
    )
    assert refs == {(None, "orders"), (None, "customers")}


def test_extract_refs_excludes_derived_table_alias_and_create_target():
    refs = extract_table_refs(
        "CREATE VIEW v AS SELECT * FROM (SELECT id FROM orders) AS sub JOIN customers ON 1 = 1",
        "sqlite",
    )
    assert refs == {(None, "orders"), (None, "customers")}  # neither "v" nor "sub"


def test_extract_refs_unparseable_returns_none():
    assert extract_table_refs("SELECT FROM WHERE (((", "sqlite") is None


def test_extract_refs_retries_generic_dialect():
    # bogus dialect -> first parse attempt raises, generic retry still succeeds
    assert extract_table_refs("SELECT * FROM orders", "not-a-dialect") == {(None, "orders")}


# ---- API: DDL + lineage graphs ----


@pytest.fixture(scope="module")
def lineage_env(client, admin_headers):
    """Second source DB: customers + orders, a join view, and a CTE view.

    customers, orders and order_totals are registered as datasets; the
    `recent` view stays unregistered on purpose (dataset_id must be null).
    (mkdtemp like conftest — pytest's tmp_path_factory trips on a locked
    pytest-of-* dir on this machine.)
    """
    path = Path(tempfile.mkdtemp(prefix="dqsentinel-lineage-")) / "lineage.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL, created_at TEXT);
        CREATE VIEW order_totals AS
            SELECT c.id AS customer_id, c.name, SUM(o.amount) AS total
            FROM orders o JOIN customers c ON c.id = o.customer_id
            GROUP BY c.id, c.name;
        CREATE VIEW recent AS
            WITH last7 AS (
                SELECT * FROM orders WHERE created_at >= '2026-06-01'
            )
            SELECT last7.id, c.name
            FROM last7 JOIN customers c ON c.id = last7.customer_id;
        INSERT INTO customers VALUES (1, 'Ada', 'ada@example.com'), (2, 'Bo', NULL);
        INSERT INTO orders VALUES (1, 1, 10.0, '2026-06-05'), (2, 2, 4.5, '2026-06-07');
        """
    )
    con.commit()
    con.close()

    resp = client.post(
        "/api/v1/connections",
        json={"name": "lineage-src", "dsn": f"sqlite:///{path.as_posix()}"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    conn_id = resp.json()["id"]

    resp = client.post(
        "/api/v1/datasets/register",
        json={
            "connection_id": conn_id,
            "tables": [
                {"table_name": "customers"},
                {"table_name": "orders"},
                {"table_name": "order_totals", "kind": "view"},
            ],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    datasets = {d["table_name"]: d["id"] for d in resp.json()}
    return {"conn_id": conn_id, "datasets": datasets}


def test_ddl_for_view_and_table(client, admin_headers, lineage_env):
    ds = lineage_env["datasets"]

    view = client.get(f"/api/v1/datasets/{ds['order_totals']}/ddl", headers=admin_headers)
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["dataset_id"] == ds["order_totals"]
    assert body["kind"] == "view"
    assert body["source"] == "database"  # sqlite keeps the real CREATE VIEW text
    assert "create view" in body["ddl"].lower()
    assert "order_totals" in body["ddl"].lower()

    table = client.get(f"/api/v1/datasets/{ds['customers']}/ddl", headers=admin_headers).json()
    assert table["dataset_id"] == ds["customers"]
    assert table["kind"] == "table"
    assert "create table" in table["ddl"].lower()
    # sqlite exposes real table DDL ("database"); engines without it synthesize one
    assert table["source"] in ("database", "synthesized")


def test_connection_lineage_full_graph(client, admin_headers, lineage_env):
    resp = client.get(
        f"/api/v1/connections/{lineage_env['conn_id']}/lineage", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    graph = resp.json()

    ids = {n["id"] for n in graph["nodes"]}
    assert ids == {"customers", "orders", "order_totals", "recent"}
    assert all(n["id"] == n["id"].lower() for n in graph["nodes"])

    edges = {(e["source"], e["target"]) for e in graph["edges"]}
    assert edges == {
        ("orders", "order_totals"),
        ("customers", "order_totals"),
        ("orders", "recent"),
        ("customers", "recent"),
    }  # CTE alias last7 must not appear as a node or an edge endpoint

    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["order_totals"]["kind"] == "view"
    assert nodes["recent"]["kind"] == "view"
    assert nodes["orders"]["kind"] == "table"
    assert nodes["orders"]["dataset_id"] == lineage_env["datasets"]["orders"]
    assert nodes["recent"]["dataset_id"] is None  # never registered
    assert graph["parse_errors"] == 0
    assert graph["truncated"] is False


def test_dataset_lineage_depth_subgraphs(client, admin_headers, lineage_env):
    ds = lineage_env["datasets"]

    # upstream direction from the join view
    g1 = client.get(
        f"/api/v1/datasets/{ds['order_totals']}/lineage?depth=1", headers=admin_headers
    ).json()
    assert {n["id"] for n in g1["nodes"]} == {"order_totals", "orders", "customers"}
    assert {(e["source"], e["target"]) for e in g1["edges"]} == {
        ("orders", "order_totals"),
        ("customers", "order_totals"),
    }

    # one more hop reaches the sibling view through a shared parent
    g2 = client.get(
        f"/api/v1/datasets/{ds['order_totals']}/lineage?depth=2", headers=admin_headers
    ).json()
    assert {n["id"] for n in g2["nodes"]} == {"order_totals", "orders", "customers", "recent"}
    assert len(g2["edges"]) == 4

    # default depth is 2
    gd = client.get(f"/api/v1/datasets/{ds['order_totals']}/lineage", headers=admin_headers).json()
    assert {n["id"] for n in gd["nodes"]} == {n["id"] for n in g2["nodes"]}

    # downstream direction: BFS follows edges both ways
    gc = client.get(
        f"/api/v1/datasets/{ds['customers']}/lineage?depth=1", headers=admin_headers
    ).json()
    assert {n["id"] for n in gc["nodes"]} == {"customers", "order_totals", "recent"}

    # depth is clamped into 1..5 instead of rejected
    g0 = client.get(
        f"/api/v1/datasets/{ds['order_totals']}/lineage?depth=0", headers=admin_headers
    ).json()
    assert {n["id"] for n in g0["nodes"]} == {n["id"] for n in g1["nodes"]}
    g99 = client.get(
        f"/api/v1/datasets/{ds['order_totals']}/lineage?depth=99", headers=admin_headers
    ).json()
    assert {n["id"] for n in g99["nodes"]} == {n["id"] for n in g2["nodes"]}


def test_lineage_endpoints_require_auth(client, lineage_env):
    ds_id = lineage_env["datasets"]["orders"]
    assert client.get(f"/api/v1/datasets/{ds_id}/ddl").status_code == 401
    assert client.get(f"/api/v1/datasets/{ds_id}/lineage").status_code == 401
    assert client.get(f"/api/v1/connections/{lineage_env['conn_id']}/lineage").status_code == 401


def test_lineage_unknown_ids_404(client, admin_headers):
    assert client.get("/api/v1/datasets/999999/ddl", headers=admin_headers).status_code == 404
    assert client.get("/api/v1/datasets/999999/lineage", headers=admin_headers).status_code == 404
    assert (
        client.get("/api/v1/connections/999999/lineage", headers=admin_headers).status_code == 404
    )


def test_lineage_health_overlay(client, admin_headers, lineage_env):
    """Seed a failing check (customers.email has a NULL) and a passing one
    (orders.id), then assert the overlay on the graph. Runs last in this module
    because it mutates check/run state for the lineage connection."""
    h = admin_headers
    ds = lineage_env["datasets"]

    check = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds["customers"],
            "check_type": "not_null",
            "column_name": "email",
            "severity": "error",
            "name": "lineage: email not null",
        },
        headers=h,
    )
    assert check.status_code == 201, check.text
    run = client.post(f"/api/v1/checks/{check.json()['id']}/run", headers=h).json()
    assert run["status"] == "fail"
    assert run["violation_count"] == 1

    ok = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds["orders"],
            "check_type": "not_null",
            "column_name": "id",
            "severity": "error",
            "name": "lineage: order id not null",
        },
        headers=h,
    )
    assert ok.status_code == 201, ok.text
    assert client.post(f"/api/v1/checks/{ok.json()['id']}/run", headers=h).json()["status"] == "pass"

    graph = client.get(
        f"/api/v1/connections/{lineage_env['conn_id']}/lineage", headers=h
    ).json()
    nodes = {n["id"]: n for n in graph["nodes"]}

    assert nodes["customers"]["health"] == "fail"
    assert nodes["customers"]["failing_checks"] >= 1
    assert nodes["customers"]["open_exceptions"] >= 1
    assert nodes["customers"]["dataset_id"] == ds["customers"]

    assert nodes["orders"]["health"] == "pass"  # ran, no failures, no open exceptions
    assert nodes["orders"]["failing_checks"] == 0

    assert nodes["order_totals"]["health"] == "unknown"  # registered but never run
    assert nodes["recent"]["health"] == "unknown"  # not even registered
