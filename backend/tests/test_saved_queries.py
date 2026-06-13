"""Saved queries (issue #41): CRUD round-trip, save-time guard_sql validation,
creator-or-admin edit/delete rule, run delegation + last_run_at stamping, filters."""


def _setup(client, headers, source_db, conn_name: str):
    """Reuse the workbench fixture shape: a connection + one registered dataset."""
    conn = client.post(
        "/api/v1/connections", json={"name": conn_name, "dsn": source_db}, headers=headers
    )
    assert conn.status_code == 201, conn.text
    conn_id = conn.json()["id"]
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn_id, "tables": [{"table_name": "people"}]},
        headers=headers,
    ).json()[0]
    return conn_id, ds["id"]


def _editor_headers(client, admin_headers, email: str):
    client.post(
        "/api/v1/auth/users",
        json={"email": email, "password": "editor123", "role": "editor"},
        headers=admin_headers,
    )
    token = client.post(
        "/api/v1/auth/login", json={"email": email, "password": "editor123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_saved_query_crud_round_trip(client, admin_headers, source_db):
    conn_id, ds_id = _setup(client, admin_headers, source_db, "sq-conn")

    # create
    created = client.post(
        "/api/v1/queries",
        json={
            "connection_id": conn_id,
            "dataset_id": ds_id,
            "name": "Active people",
            "description": "people grouped by status",
            "sql": "SELECT status, COUNT(*) AS n FROM people GROUP BY 1",
            "tags": ["triage", "people"],
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    q = created.json()
    qid = q["id"]
    assert q["name"] == "Active people"
    assert q["dataset_id"] == ds_id
    assert q["tags"] == ["triage", "people"]
    assert q["created_by"]  # resolved display name/email
    assert q["last_run_at"] is None

    # read in list
    listing = client.get(f"/api/v1/queries?connection_id={conn_id}", headers=admin_headers).json()
    assert any(item["id"] == qid for item in listing)

    # patch
    patched = client.patch(
        f"/api/v1/queries/{qid}",
        json={"name": "Renamed", "tags": ["triage"]},
        headers=admin_headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["tags"] == ["triage"]

    # unpin clears the dataset
    unpinned = client.patch(
        f"/api/v1/queries/{qid}", json={"unpin": True}, headers=admin_headers
    )
    assert unpinned.status_code == 200, unpinned.text
    assert unpinned.json()["dataset_id"] is None

    # delete
    assert client.delete(f"/api/v1/queries/{qid}", headers=admin_headers).status_code == 204
    listing = client.get(f"/api/v1/queries?connection_id={conn_id}", headers=admin_headers).json()
    assert not any(item["id"] == qid for item in listing)


def test_get_by_id_and_404(client, admin_headers, source_db):
    conn_id, _ = _setup(client, admin_headers, source_db, "sq-getid")
    qid = client.post(
        "/api/v1/queries",
        json={"connection_id": conn_id, "name": "deep-link target", "sql": "SELECT 1"},
        headers=admin_headers,
    ).json()["id"]

    got = client.get(f"/api/v1/queries/{qid}", headers=admin_headers)
    assert got.status_code == 200, got.text
    assert got.json()["name"] == "deep-link target"
    assert got.json()["sql"] == "SELECT 1"

    assert client.get("/api/v1/queries/99999", headers=admin_headers).status_code == 404


def test_save_rejects_non_select_sql(client, admin_headers, source_db):
    conn_id, _ = _setup(client, admin_headers, source_db, "sq-guard")

    resp = client.post(
        "/api/v1/queries",
        json={"connection_id": conn_id, "name": "bad", "sql": "DELETE FROM people"},
        headers=admin_headers,
    )
    assert resp.status_code == 422, resp.text

    # and a valid save can't be patched into a non-SELECT
    qid = client.post(
        "/api/v1/queries",
        json={"connection_id": conn_id, "name": "ok", "sql": "SELECT 1"},
        headers=admin_headers,
    ).json()["id"]
    bad_patch = client.patch(
        f"/api/v1/queries/{qid}", json={"sql": "DROP TABLE people"}, headers=admin_headers
    )
    assert bad_patch.status_code == 422, bad_patch.text


def test_creator_or_admin_edit_rule(client, admin_headers, source_db):
    conn_id, _ = _setup(client, admin_headers, source_db, "sq-perm")
    a = _editor_headers(client, admin_headers, "sq-editor-a@example.com")
    b = _editor_headers(client, admin_headers, "sq-editor-b@example.com")

    qid = client.post(
        "/api/v1/queries",
        json={"connection_id": conn_id, "name": "A's query", "sql": "SELECT 1"},
        headers=a,
    ).json()["id"]

    # editor B may not edit or delete A's query
    assert (
        client.patch(f"/api/v1/queries/{qid}", json={"name": "hijack"}, headers=b).status_code
        == 403
    )
    assert client.delete(f"/api/v1/queries/{qid}", headers=b).status_code == 403

    # admin may
    assert (
        client.patch(
            f"/api/v1/queries/{qid}", json={"name": "by admin"}, headers=admin_headers
        ).status_code
        == 200
    )
    assert client.delete(f"/api/v1/queries/{qid}", headers=admin_headers).status_code == 204


def test_run_saved_query_stamps_last_run_at(client, admin_headers, source_db):
    conn_id, _ = _setup(client, admin_headers, source_db, "sq-run")
    qid = client.post(
        "/api/v1/queries",
        json={
            "connection_id": conn_id,
            "name": "count",
            "sql": "SELECT status, COUNT(*) AS n FROM people GROUP BY 1",
        },
        headers=admin_headers,
    ).json()["id"]

    run = client.post(f"/api/v1/queries/{qid}/run", headers=admin_headers)
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["columns"] == ["status", "n"]
    assert body["row_count"] >= 2

    # last_run_at is now stamped
    listing = client.get(f"/api/v1/queries?connection_id={conn_id}", headers=admin_headers).json()
    mine = next(item for item in listing if item["id"] == qid)
    assert mine["last_run_at"] is not None

    # limit caps rows / reports truncation, same as the workbench path
    full = client.post(
        "/api/v1/queries",
        json={"connection_id": conn_id, "name": "all", "sql": "SELECT * FROM people"},
        headers=admin_headers,
    ).json()
    capped = client.post(f"/api/v1/queries/{full['id']}/run?limit=10", headers=admin_headers)
    assert capped.status_code == 200
    assert capped.json()["row_count"] == 10
    assert capped.json()["truncated"] is True


def test_run_requires_editor(client, admin_headers, source_db):
    conn_id, _ = _setup(client, admin_headers, source_db, "sq-role")
    qid = client.post(
        "/api/v1/queries",
        json={"connection_id": conn_id, "name": "x", "sql": "SELECT 1"},
        headers=admin_headers,
    ).json()["id"]

    client.post(
        "/api/v1/auth/users",
        json={"email": "sq-viewer@example.com", "password": "viewer123", "role": "viewer"},
        headers=admin_headers,
    )
    token = client.post(
        "/api/v1/auth/login", json={"email": "sq-viewer@example.com", "password": "viewer123"}
    ).json()["access_token"]
    vh = {"Authorization": f"Bearer {token}"}

    # viewers can read the shared library …
    assert client.get("/api/v1/queries", headers=vh).status_code == 200
    # … but cannot create or run
    assert (
        client.post(
            "/api/v1/queries",
            json={"connection_id": conn_id, "name": "nope", "sql": "SELECT 1"},
            headers=vh,
        ).status_code
        == 403
    )
    assert client.post(f"/api/v1/queries/{qid}/run", headers=vh).status_code == 403


def test_filters_dataset_tag_and_q(client, admin_headers, source_db):
    conn_id, ds_id = _setup(client, admin_headers, source_db, "sq-filter")

    pinned = client.post(
        "/api/v1/queries",
        json={
            "connection_id": conn_id,
            "dataset_id": ds_id,
            "name": "Pinned revenue probe",
            "description": "looks at revenue",
            "sql": "SELECT 1",
            "tags": ["finance"],
        },
        headers=admin_headers,
    ).json()
    unpinned = client.post(
        "/api/v1/queries",
        json={
            "connection_id": conn_id,
            "name": "Loose latency probe",
            "description": "looks at latency",
            "sql": "SELECT 2",
            "tags": ["perf"],
        },
        headers=admin_headers,
    ).json()

    # dataset_id filter -> only the pinned one
    ds_hits = client.get(
        f"/api/v1/queries?dataset_id={ds_id}", headers=admin_headers
    ).json()
    ids = {item["id"] for item in ds_hits}
    assert pinned["id"] in ids and unpinned["id"] not in ids

    # tag filter
    tag_hits = client.get(
        f"/api/v1/queries?connection_id={conn_id}&tag=perf", headers=admin_headers
    ).json()
    tag_ids = {item["id"] for item in tag_hits}
    assert unpinned["id"] in tag_ids and pinned["id"] not in tag_ids

    # q filter (name/description ilike)
    q_hits = client.get(
        f"/api/v1/queries?connection_id={conn_id}&q=revenue", headers=admin_headers
    ).json()
    q_ids = {item["id"] for item in q_hits}
    assert pinned["id"] in q_ids and unpinned["id"] not in q_ids
