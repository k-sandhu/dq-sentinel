"""Built-in data catalog: listing, one-click connect materializes a fully-governed
dataset (profile + knowledge + active contract + active checks + SLAs) with NO run
history, idempotent re-connect, and role gating.

The backing DB files are generated into a temp dir (DQ_CATALOG_DATA_DIR) so the
test never writes into the repo's samples/.
"""

import contextlib
import os
import sqlite3
import tempfile

import pytest

# definitions/generators are pure data; app.catalog.seed is imported lazily inside the
# test that needs it (importing it at module scope pulls in app.api -> circular import).
from app.catalog import definitions, generators
from app.config import get_settings

API = "/api/v1/catalog"
ENTRY = "retail-commerce"  # sqlite, 2 datasets (orders + customers) — rich + fast
SOURCE_NAME = "Retail — Commerce DB"


@pytest.fixture(scope="module", autouse=True)
def _catalog_dir():
    # tempfile.mkdtemp (not pytest's tmp_path_factory) mirrors conftest and avoids
    # the sandbox's scandir restriction on the pytest temp base dir.
    d = tempfile.mkdtemp(prefix="dqsentinel-catalog-")
    os.environ["DQ_CATALOG_DATA_DIR"] = d
    get_settings.cache_clear()
    yield
    os.environ.pop("DQ_CATALOG_DATA_DIR", None)
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def editor_headers(client, admin_headers) -> dict[str, str]:
    client.post(
        "/api/v1/auth/users",
        json={"email": "cat-editor@example.com", "password": "editor123", "role": "editor"},
        headers=admin_headers,
    )
    resp = client.post(
        "/api/v1/auth/login", json={"email": "cat-editor@example.com", "password": "editor123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def viewer_headers(client, admin_headers) -> dict[str, str]:
    client.post(
        "/api/v1/auth/users",
        json={"email": "cat-viewer@example.com", "password": "viewer123", "role": "viewer"},
        headers=admin_headers,
    )
    resp = client.post(
        "/api/v1/auth/login", json={"email": "cat-viewer@example.com", "password": "viewer123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _checks(client, headers, dataset_id):
    raw = client.get(f"/api/v1/checks?dataset_id={dataset_id}", headers=headers).json()
    return raw if isinstance(raw, list) else raw.get("items", [])


def test_list_catalog(client, admin_headers):
    rows = client.get(API, headers=admin_headers).json()
    assert isinstance(rows, list) and len(rows) == 7, rows
    by_key = {r["key"] for r in rows}
    assert {"retail-commerce", "finance-payments", "people-hris", "marketing-clickstream",
            "supplychain-logistics", "healthcare-ehr", "product-subscriptions"} == by_key
    # Every entry previews governance metadata and starts disconnected.
    assert all(r["connected"] is False for r in rows)
    hris = next(r for r in rows if r["key"] == "people-hris")
    assert hris["pii"] is True and hris["domain"] == "People" and hris["owner"]
    assert any(t["pii"] for t in hris["tables"])
    clickstream = next(r for r in rows if r["key"] == "marketing-clickstream")
    assert clickstream["engine"] == "duckdb"


def test_list_requires_auth(client):
    assert client.get(API).status_code == 401


def test_connect_requires_editor(client, viewer_headers):
    assert client.post(f"{API}/{ENTRY}/connect", headers=viewer_headers).status_code == 403


def test_connect_unknown_key(client, editor_headers):
    assert client.post(f"{API}/nope/connect", headers=editor_headers).status_code == 404


def test_connect_materializes_full_bundle(client, editor_headers, admin_headers):
    resp = client.post(f"{API}/{ENTRY}/connect", headers=editor_headers)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["connected"] is True and out["connection_id"]
    assert out["table_count"] == 2 and out["check_count"] > 0
    assert out["has_contract"] is True and out["importance"] == "critical" and out["pii"] is True

    # Connection exists under the catalog's source-system name.
    conns = client.get("/api/v1/connections", headers=admin_headers).json()
    conn = next(c for c in conns if c["name"] == SOURCE_NAME)
    cid = conn["id"]

    # Both tables registered, profiled (row_count + last_profiled_at), zero exceptions.
    datasets = client.get(f"/api/v1/datasets?connection_id={cid}", headers=admin_headers).json()
    by_table = {d["table_name"]: d for d in datasets}
    assert {"orders", "customers"} <= set(by_table)
    for ds in datasets:
        assert ds["row_count"] and ds["row_count"] > 0
        assert ds["last_profiled_at"]
        assert ds["open_exceptions"] == 0  # "configured, no run history"

    orders = by_table["orders"]
    oid = orders["id"]

    # Rich knowledge populated.
    k = client.get(f"/api/v1/datasets/{oid}/knowledge", headers=admin_headers).json()
    assert k["importance"] == "critical" and k["owner"] == "Maria Gomez"
    assert k["domain"] == "Commerce" and k["freshness_sla_hours"] == 24
    assert k["business_context"] and k["known_issues"]
    cust_k = client.get(
        f"/api/v1/datasets/{by_table['customers']['id']}/knowledge", headers=admin_headers
    ).json()
    assert "email" in cust_k["pii_columns"]

    # Active data contract.
    contract = client.get(f"/api/v1/datasets/{oid}/contract", headers=admin_headers).json()
    assert contract["status"] == "active"
    assert contract["spec"]["quality"], "contract should carry governance clauses"

    # Checks: a mix of system monitors, contract clauses, and a curated custom_sql —
    # all active, none run yet, and EXACTLY ONE freshness check (no monitor/contract dup).
    checks = _checks(client, admin_headers, oid)
    assert checks, "orders should have checks"
    assert all(c["status"] == "active" for c in checks)
    assert all(not c.get("last_run_at") for c in checks), "no check should have run"
    freshness = [c for c in checks if c["check_type"] == "freshness"]
    assert len(freshness) == 1, f"expected one freshness check, got {len(freshness)}"
    assert any(c["check_type"] == "custom_sql" for c in checks)
    origins = {c.get("origin") for c in checks}
    assert {"system", "contract", "manual"} <= origins, origins

    # SLAs created (freshness via knowledge + a check_success target).
    slas = client.get("/api/v1/sla", headers=admin_headers).json()
    ds_ids = {d["id"] for d in datasets}
    dataset_slas = [s for s in slas if s["scope"] == "dataset" and s["scope_id"] in ds_ids]
    assert any(s["target_type"] == "freshness" for s in dataset_slas)
    assert any(s["target_type"] == "check_success" for s in dataset_slas)


def test_connect_is_idempotent(client, editor_headers, admin_headers):
    first = client.post(f"{API}/{ENTRY}/connect", headers=editor_headers).json()
    conns = client.get("/api/v1/connections", headers=admin_headers).json()
    matching = [c for c in conns if c["name"] == SOURCE_NAME]
    assert len(matching) == 1, "re-connect must not create a second connection"
    cid = matching[0]["id"]
    assert first["connection_id"] == cid
    datasets = client.get(f"/api/v1/datasets?connection_id={cid}", headers=admin_headers).json()
    assert len(datasets) == 2  # stable, not duplicated

    # And it now shows connected in the catalog listing.
    rows = client.get(API, headers=admin_headers).json()
    retail = next(r for r in rows if r["key"] == ENTRY)
    assert retail["connected"] is True and retail["connection_id"] == cid


def test_connect_duckdb_entry(client, editor_headers, admin_headers):
    """The DuckDB analytics entry generates, profiles, and materializes checks too
    (exercises the multi-engine + single-writer path)."""
    key, name = "marketing-clickstream", "Marketing — Web Analytics"
    resp = client.post(f"{API}/{key}/connect", headers=editor_headers)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["connected"] is True and out["engine"] == "duckdb"
    cid = out["connection_id"]
    datasets = client.get(f"/api/v1/datasets?connection_id={cid}", headers=admin_headers).json()
    assert len(datasets) == 1 and datasets[0]["table_name"] == "web_events"
    assert datasets[0]["row_count"] and datasets[0]["row_count"] > 0
    checks = _checks(client, admin_headers, datasets[0]["id"])
    assert checks and all(c["status"] == "active" for c in checks)
    # clean up so it doesn't bleed into other modules' connection listings
    assert client.delete(f"{API}/{key}/disconnect", headers=admin_headers).status_code == 204
    assert all(
        c["name"] != name
        for c in client.get("/api/v1/connections", headers=admin_headers).json()
    )


def test_disconnect_requires_admin_and_cleans_up(client, editor_headers, admin_headers):
    # editor cannot disconnect
    assert client.delete(f"{API}/{ENTRY}/disconnect", headers=editor_headers).status_code == 403
    # admin disconnects -> connection and dependents gone
    assert client.delete(f"{API}/{ENTRY}/disconnect", headers=admin_headers).status_code == 204
    conns = client.get("/api/v1/connections", headers=admin_headers).json()
    assert all(c["name"] != SOURCE_NAME for c in conns)
    rows = client.get(API, headers=admin_headers).json()
    retail = next(r for r in rows if r["key"] == ENTRY)
    assert retail["connected"] is False and retail["connection_id"] is None
    # disconnect again is a no-op (still 204)
    assert client.delete(f"{API}/{ENTRY}/disconnect", headers=admin_headers).status_code == 204


def test_name_collision_with_user_connection_is_never_adopted(client, editor_headers, admin_headers):
    """A user connection that merely shares the entry's source-system name is not
    the catalog's: connect must fail loudly (not report success against foreign
    data) and disconnect must not cascade-delete it."""
    decoy = client.post(
        "/api/v1/connections",
        json={"name": SOURCE_NAME, "dsn": "sqlite:///decoy-not-catalog.sqlite"},
        headers=admin_headers,
    ).json()
    try:
        # Catalog still reports the entry as disconnected (foreign conn not adopted).
        retail = next(r for r in client.get(API, headers=admin_headers).json() if r["key"] == ENTRY)
        assert retail["connected"] is False and retail["connection_id"] is None

        # Connect refuses with a clear 422 instead of adopting or duplicating.
        resp = client.post(f"{API}/{ENTRY}/connect", headers=editor_headers)
        assert resp.status_code == 422, resp.text
        assert "not created from the catalog" in resp.json()["detail"]

        # Disconnect is a no-op 204 and the user's connection SURVIVES.
        assert client.delete(f"{API}/{ENTRY}/disconnect", headers=admin_headers).status_code == 204
        conns = client.get("/api/v1/connections", headers=admin_headers).json()
        assert any(c["id"] == decoy["id"] for c in conns), "user connection must not be deleted"
    finally:
        client.delete(f"/api/v1/connections/{decoy['id']}", headers=admin_headers)


# --------------------------------------------------------------------------- #
# Curated-clause integrity (#275): a clause must not certify rows the catalog   #
# itself plants as defects, and its wording must not overstate its params.      #
# --------------------------------------------------------------------------- #

def _range_clause(entry_key: str, table_name: str, column: str):
    entry = definitions.entry_by_key(entry_key)
    assert entry is not None, entry_key
    table = next(t for t in entry.tables if t.table_name == table_name)
    return next(q for q in table.quality if q.check_type == "range" and q.column == column)


@pytest.mark.parametrize(
    ("entry_key", "table_name", "column", "planted"),
    [
        ("product-subscriptions", "subscriptions", "seats", generators._INVALID_SEATS),
        ("marketing-clickstream", "web_events", "duration_ms", generators._INVALID_DURATIONS),
    ],
)
def test_range_clause_catches_every_planted_invalid_value(entry_key, table_name, column, planted):
    """Every value the generator plants as invalid must violate the curated clause
    guarding that column. Mirrors the runner's bounds exactly (`col < min` /
    `col > max` — both bounds inclusive), so `{"min": 0}` does NOT flag a 0."""
    clause = _range_clause(entry_key, table_name, column)
    lo, hi = clause.params.get("min"), clause.params.get("max")
    assert planted, f"{clause.id}: nothing planted to catch"
    for value in planted:
        flagged = (lo is not None and value < lo) or (hi is not None and value > hi)
        assert flagged, (
            f"{clause.id} ({clause.name}) with params {clause.params} PASSES {column}={value}, "
            "which the generator plants as an invalid value"
        )


def test_range_clause_wording_matches_its_bound():
    """A clause name/rationale may not promise more than its params encode: a
    'positive' clause needs min >= 1, a 'non-negative' one min == 0, and no clause
    may claim both."""
    for entry in definitions.CATALOG:
        for table in entry.tables:
            for q in table.quality:
                if q.check_type != "range":
                    continue
                text = f"{q.id} {q.name} {q.rationale}".lower()
                nonneg = "non-negative" in text or "nonneg" in text
                positive = "positive" in text
                where = f"{entry.key}/{table.table_name}/{q.id}"
                assert not (nonneg and positive), (
                    f"{where}: wording claims both 'positive' and 'non-negative' — "
                    f"pick one and match the params ({q.params})"
                )
                if positive:
                    assert q.params.get("min") is not None and q.params["min"] >= 1, (
                        f"{where}: named 'positive' but params {q.params} admit 0"
                    )
                if nonneg:
                    assert q.params.get("min") == 0, (
                        f"{where}: named 'non-negative' but params are {q.params}"
                    )


def test_seats_check_flags_every_planted_bad_seat_row(client, editor_headers, admin_headers):
    """End-to-end: the contract clause materializes into a check whose SQL flags all
    of 0/-1/-5 in the generated data — with `{"min": 0}` the 0-seat rows pass."""
    from app.catalog import seed

    key = "product-subscriptions"
    resp = client.post(f"{API}/{key}/connect", headers=editor_headers)
    assert resp.status_code == 200, resp.text
    cid = resp.json()["connection_id"]
    try:
        datasets = client.get(
            f"/api/v1/datasets?connection_id={cid}", headers=admin_headers
        ).json()
        ds = next(d for d in datasets if d["table_name"] == "subscriptions")
        checks = _checks(client, admin_headers, ds["id"])
        seats = next(
            c for c in checks if c["check_type"] == "range" and c["column_name"] == "seats"
        )
        assert seats["origin"] == "contract", "seats check should come from the contract clause"

        path = seed.db_path(definitions.entry_by_key(key))
        with contextlib.closing(sqlite3.connect(str(path))) as con:
            planted = dict(
                con.execute(
                    "SELECT seats, COUNT(*) FROM subscriptions WHERE seats <= 0 GROUP BY seats"
                ).fetchall()
            )
        assert set(planted) == set(generators._INVALID_SEATS), planted
        assert planted[0] > 0, "generator should plant 0-seat subscriptions"

        run = client.post(f"/api/v1/checks/{seats['id']}/run", headers=editor_headers)
        assert run.status_code == 200, run.text
        out = run.json()
        assert out["violation_count"] == sum(planted.values()), (
            f"expected every seats<=0 row flagged ({planted}), got {out['violation_count']}"
        )
        assert out["status"] == "fail"
        assert seats["params"].get("min") == 1, "clause bound must be strictly positive"
    finally:
        client.delete(f"{API}/{key}/disconnect", headers=admin_headers)
