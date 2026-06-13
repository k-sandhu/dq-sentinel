"""Workbench: query run, schema/DDL introspection, suggestions, search/totals."""


def _setup_dataset(client, h, source_db, conn_name: str):
    conn = client.post("/api/v1/connections", json={"name": conn_name, "dsn": source_db}, headers=h)
    assert conn.status_code == 201, conn.text
    conn_id = conn.json()["id"]
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn_id, "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]
    assert client.post(f"/api/v1/datasets/{ds['id']}/profile", headers=h).status_code == 200
    return conn_id, ds["id"]


def test_query_run_and_guard(client, admin_headers, source_db):
    conn_id, _ = _setup_dataset(client, admin_headers, source_db, "wb-conn")

    resp = client.post(
        "/api/v1/query/run",
        json={"connection_id": conn_id, "sql": "SELECT status, COUNT(*) AS n FROM people GROUP BY 1"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["columns"] == ["status", "n"]
    assert body["row_count"] >= 2
    assert body["elapsed_ms"] >= 0
    assert body["truncated"] is False

    # row cap reported as truncation
    resp = client.post(
        "/api/v1/query/run",
        json={"connection_id": conn_id, "sql": "SELECT * FROM people", "limit": 10},
        headers=admin_headers,
    )
    assert resp.json()["truncated"] is True
    assert resp.json()["row_count"] == 10

    # writes rejected with 422
    resp = client.post(
        "/api/v1/query/run",
        json={"connection_id": conn_id, "sql": "DELETE FROM people"},
        headers=admin_headers,
    )
    assert resp.status_code == 422

    # broken SQL is a 400 with the driver message, not a 500
    resp = client.post(
        "/api/v1/query/run",
        json={"connection_id": conn_id, "sql": "SELECT nope FROM people"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


def test_query_run_requires_editor(client, admin_headers, source_db):
    client.post(
        "/api/v1/auth/users",
        json={"email": "wb-viewer@example.com", "password": "viewer123", "role": "viewer"},
        headers=admin_headers,
    )
    token = client.post(
        "/api/v1/auth/login", json={"email": "wb-viewer@example.com", "password": "viewer123"}
    ).json()["access_token"]
    vh = {"Authorization": f"Bearer {token}"}
    resp = client.post(
        "/api/v1/query/run", json={"connection_id": 1, "sql": "SELECT 1"}, headers=vh
    )
    assert resp.status_code == 403


def test_schema_and_ddl(client, admin_headers, source_db):
    conn_id, _ = _setup_dataset(client, admin_headers, source_db, "wb-conn-2")

    tree = client.get(f"/api/v1/connections/{conn_id}/schema", headers=admin_headers).json()
    people = next(t for t in tree if t["table_name"] == "people")
    assert {c["name"] for c in people["columns"]} >= {"id", "email", "age", "status"}

    ddl = client.get(
        f"/api/v1/connections/{conn_id}/ddl?table=people", headers=admin_headers
    ).json()
    assert ddl["source"] == "database"  # sqlite_master has the real definition
    assert "CREATE TABLE" in ddl["ddl"].upper()
    assert "email" in ddl["ddl"]

    resp = client.get(
        f"/api/v1/connections/{conn_id}/ddl?table=nonexistent", headers=admin_headers
    )
    assert resp.status_code == 404


def test_suggestions_heuristic_and_runnable(client, admin_headers, source_db):
    conn_id, ds_id = _setup_dataset(client, admin_headers, source_db, "wb-conn-3")

    resp = client.post("/api/v1/query/suggest", json={"dataset_id": ds_id}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "heuristic"  # no LLM key in tests
    assert body["connection_id"] == conn_id
    assert len(body["suggestions"]) >= 3
    # every suggestion must actually execute
    for s in body["suggestions"]:
        run = client.post(
            "/api/v1/query/run",
            json={"connection_id": conn_id, "sql": s["sql"], "limit": 50},
            headers=admin_headers,
        )
        assert run.status_code == 200, f"{s['title']} failed: {run.text}"

    # failure context: create + run a failing check, then suggest from its exception
    check = client.post(
        "/api/v1/checks",
        json={"dataset_id": ds_id, "check_type": "not_null", "column_name": "email", "severity": "error"},
        headers=admin_headers,
    ).json()
    run = client.post(f"/api/v1/checks/{check['id']}/run", headers=admin_headers).json()
    excs = client.get(f"/api/v1/exceptions?run_id={run['id']}", headers=admin_headers).json()["items"]
    resp = client.post(
        "/api/v1/query/suggest", json={"exception_id": excs[0]["id"]}, headers=admin_headers
    )
    body = resp.json()
    titles = " ".join(s["title"].lower() for s in body["suggestions"])
    assert "null" in titles  # check-type-specific suggestion present
    for s in body["suggestions"]:
        assert client.post(
            "/api/v1/query/run",
            json={"connection_id": conn_id, "sql": s["sql"], "limit": 50},
            headers=admin_headers,
        ).status_code == 200, s["title"]


def test_search_and_totals(client, admin_headers):
    # datasets ?q=
    hits = client.get("/api/v1/datasets?q=people", headers=admin_headers).json()
    assert hits and all("people" in d["table_name"] for d in hits)
    assert client.get("/api/v1/datasets?q=zzz-nope", headers=admin_headers).json() == []

    # exceptions carry a total in the paged envelope (#57; X-Total-Count dropped)
    page = client.get("/api/v1/exceptions?limit=1", headers=admin_headers).json()
    assert page["total"] >= len(page["items"])
    assert page["limit"] == 1

    # checks ?q=
    hits = client.get("/api/v1/checks?q=email", headers=admin_headers).json()
    assert hits and all("email" in (c["column_name"] or "") or "email" in c["name"] for c in hits)
