"""Ad-hoc dashboards: heuristic generation, execution, persistence."""

from app.connectors.sa import Connector
from app.core.adhoc import execute_panels, heuristic_panels, normalize_panels
from app.core.profiler import profile_dataset


def test_heuristic_panels_execute(source_db):
    connector = Connector(source_db)
    profile = profile_dataset(connector, "people", None, sample_rows=5000)
    panels = heuristic_panels(connector, connector.table_ref("people"), profile)
    assert 3 <= len(panels) <= 6
    types = {p["viz"]["type"] for p in panels}
    assert "number" in types  # total rows panel always present

    results = execute_panels(connector, panels)
    assert all(r["error"] is None for r in results), [r["error"] for r in results]
    total = next(r for r in results if r["title"] == "Total rows")
    assert total["rows"][0][0] == 200


def test_normalize_panels_drops_bad_sql():
    raw = [
        {"title": "ok", "sql": "SELECT 1 AS n", "viz": {"type": "number", "x": None, "y": "n"}},
        {"title": "write", "sql": "DELETE FROM x", "viz": {"type": "table"}},
        {"title": "weird viz", "sql": "SELECT 2 AS n", "viz": {"type": "hologram"}},
    ]
    panels = normalize_panels(raw)
    assert [p["title"] for p in panels] == ["ok", "weird viz"]
    assert panels[1]["viz"]["type"] == "table"  # unknown viz coerced


def test_dashboard_api_flow(client, admin_headers, source_db):
    conn = client.post(
        "/api/v1/connections", json={"name": "dash-conn", "dsn": source_db}, headers=admin_headers
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=admin_headers,
    ).json()[0]

    # generating before profiling is a 409
    resp = client.post(
        "/api/v1/adhoc-dashboards/generate", json={"dataset_id": ds["id"]}, headers=admin_headers
    )
    assert resp.status_code == 409

    client.post(f"/api/v1/datasets/{ds['id']}/profile", headers=admin_headers)
    resp = client.post(
        "/api/v1/adhoc-dashboards/generate",
        json={"dataset_id": ds["id"], "focus": "missing emails"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    dash = resp.json()
    assert dash["origin"] == "heuristic"  # no LLM key in tests
    assert dash["panel_count"] == len(dash["panels"]) > 0
    assert all(p["error"] is None for p in dash["panels"])
    assert any(p["viz"]["type"] == "number" for p in dash["panels"])

    metas = client.get(
        f"/api/v1/adhoc-dashboards?dataset_id={ds['id']}", headers=admin_headers
    ).json()
    assert any(m["id"] == dash["id"] for m in metas)
    assert metas[0]["panel_count"] > 0

    # opening re-executes panels with fresh data
    opened = client.get(f"/api/v1/adhoc-dashboards/{dash['id']}", headers=admin_headers).json()
    assert opened["panels"][0]["columns"]

    assert client.delete(
        f"/api/v1/adhoc-dashboards/{dash['id']}", headers=admin_headers
    ).status_code == 204
    assert client.get(
        f"/api/v1/adhoc-dashboards/{dash['id']}", headers=admin_headers
    ).status_code == 404
