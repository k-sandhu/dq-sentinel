"""Object-level authorization enforcement (#159).

Per-connection grants scope every data surface: a user granted access to some
connections sees ONLY those; admins and zero-grant (legacy) users see everything.
Grant *role* also caps mutation — a viewer-grant cannot run workbench SQL.

Uses globally-unique connection/user names because the app DB is shared across
the test session.
"""

from uuid import uuid4

QH = "/api/v1"


def _mk_user(client, admin_headers, email, role="editor"):
    return client.post(
        f"{QH}/auth/users",
        json={"email": email, "name": email, "password": "password1", "role": role},
        headers=admin_headers,
    ).json()


def _login(client, email):
    tok = client.post(f"{QH}/auth/login", json={"email": email, "password": "password1"}).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


def _grant(client, admin_headers, user_id, connection_id, role="editor"):
    r = client.post(
        f"{QH}/auth/users/{user_id}/grants",
        json={"connection_id": connection_id, "role": role},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text


def _conn(client, admin_headers, name, source_db):
    return client.post(
        f"{QH}/connections", json={"name": name, "dsn": source_db}, headers=admin_headers
    ).json()


def _register_people(client, admin_headers, connection_id):
    return client.post(
        f"{QH}/datasets/register",
        json={"connection_id": connection_id, "tables": [{"table_name": "people"}]},
        headers=admin_headers,
    ).json()[0]


def test_grants_scope_connections_datasets_query_and_search(client, admin_headers, source_db):
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"authz-A-{sfx}", source_db)
    b = _conn(client, h, f"authz-B-{sfx}", source_db)
    dsa = _register_people(client, h, a["id"])
    dsb = _register_people(client, h, b["id"])

    alice = _mk_user(client, h, f"authz-alice-{sfx}@x.com")  # editor, granted A
    _mk_user(client, h, f"authz-nory-{sfx}@x.com")  # editor, NO grants (legacy)
    carol = _mk_user(client, h, f"authz-carol-{sfx}@x.com")  # editor, granted only VIEWER on A
    _grant(client, h, alice["id"], a["id"], "editor")
    _grant(client, h, carol["id"], a["id"], "viewer")
    ah, nh, ch = (_login(client, u) for u in
                  (f"authz-alice-{sfx}@x.com", f"authz-nory-{sfx}@x.com", f"authz-carol-{sfx}@x.com"))

    # Connections list: alice sees only A; nory (zero-grant) + admin see both.
    a_conns = {c["id"] for c in client.get(f"{QH}/connections", headers=ah).json()}
    assert a["id"] in a_conns and b["id"] not in a_conns
    n_conns = {c["id"] for c in client.get(f"{QH}/connections", headers=nh).json()}
    assert {a["id"], b["id"]} <= n_conns
    admin_conns = {c["id"] for c in client.get(f"{QH}/connections", headers=h).json()}
    assert {a["id"], b["id"]} <= admin_conns

    # Connection by-id introspection: 404 (not 403) for an ungranted connection.
    assert client.get(f"{QH}/connections/{b['id']}/tables", headers=ah).status_code == 404
    assert client.get(f"{QH}/connections/{a['id']}/tables", headers=ah).status_code == 200

    # Datasets: alice sees only A's dataset; by-id 404 on B's.
    a_ds = {d["id"] for d in client.get(f"{QH}/datasets", headers=ah).json()}
    assert dsa["id"] in a_ds and dsb["id"] not in a_ds
    assert client.get(f"{QH}/datasets/{dsb['id']}", headers=ah).status_code == 404
    assert client.get(f"{QH}/datasets/{dsa['id']}", headers=ah).status_code == 200

    # /query/run: alice 404 against B, 200 against A. nory (legacy) 200 against B.
    def run(headers, conn_id):
        return client.post(
            f"{QH}/query/run",
            json={"connection_id": conn_id, "sql": "SELECT 1", "limit": 5},
            headers=headers,
        ).status_code

    assert run(ah, b["id"]) == 404
    assert run(ah, a["id"]) == 200
    assert run(nh, b["id"]) == 200  # zero-grant legacy: unrestricted

    # Grant role caps mutation: carol can SEE A (viewer) but not run SQL on it (403).
    assert client.get(f"{QH}/connections/{a['id']}/tables", headers=ch).status_code == 200
    assert run(ch, a["id"]) == 403

    # Search: alice discovers A's connection, never B's.
    hits = client.get(f"{QH}/search", params={"q": "authz-"}, headers=ah).json()["hits"]
    names = {hit["title"] for hit in hits}
    assert f"authz-A-{sfx}" in names and f"authz-B-{sfx}" not in names

    # Clean up so seeded data doesn't skew global counts in the shared app DB
    # (deleting a connection cascades datasets/checks/runs/exceptions + grants).
    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


def test_grants_scope_runs_and_exceptions(client, admin_headers, source_db):
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"authz-re-A-{sfx}", source_db)
    b = _conn(client, h, f"authz-re-B-{sfx}", source_db)
    dsb = _register_people(client, h, b["id"])

    # Real run + exceptions on B (not_null on the email column, which has nulls).
    chk = client.post(
        f"{QH}/checks",
        json={"dataset_id": dsb["id"], "check_type": "not_null", "column_name": "email",
              "name": f"authz-chk-{sfx}"},
        headers=h,
    ).json()
    assert client.post(f"{QH}/checks/{chk['id']}/run", headers=h).status_code in (200, 201)

    alice = _mk_user(client, h, f"authz-re-alice-{sfx}@x.com")  # granted A only
    _grant(client, h, alice["id"], a["id"], "editor")
    ah = _login(client, f"authz-re-alice-{sfx}@x.com")

    # Admin can see B's run; alice (granted A only) cannot — by-id 404 + absent from list.
    b_runs = client.get(f"{QH}/runs", params={"dataset_id": dsb["id"]}, headers=h).json()
    assert b_runs, "admin should see B's run"
    b_run_id = b_runs[0]["id"]
    assert client.get(f"{QH}/runs/{b_run_id}", headers=ah).status_code == 404
    assert b_run_id not in {r["id"] for r in client.get(f"{QH}/runs", headers=ah).json()}
    assert client.get(f"{QH}/runs/{b_run_id}/exceptions", headers=ah).status_code == 404

    # Exceptions list is scoped too: B's exceptions are invisible to alice.
    admin_exc = client.get(f"{QH}/exceptions", params={"dataset_id": dsb["id"]}, headers=h).json()
    assert admin_exc["total"] >= 1
    alice_exc = client.get(f"{QH}/exceptions", params={"dataset_id": dsb["id"]}, headers=ah).json()
    assert alice_exc["total"] == 0

    # Clean up the failing check/run/exceptions so they don't skew global console counts.
    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


def test_exception_mutation_requires_editor_on_connection(client, admin_headers, source_db):
    """A viewer-grant can READ an exception but not change its triage state — mutation
    requires editor ON the connection, consistent with /query/run etc. (#159)."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    b = _conn(client, h, f"authz-mut-B-{sfx}", source_db)
    dsb = _register_people(client, h, b["id"])
    chk = client.post(
        f"{QH}/checks",
        json={"dataset_id": dsb["id"], "check_type": "not_null", "column_name": "email",
              "name": f"authz-mut-chk-{sfx}"},
        headers=h,
    ).json()
    assert client.post(f"{QH}/checks/{chk['id']}/run", headers=h).status_code in (200, 201)
    items = client.get(f"{QH}/exceptions", params={"dataset_id": dsb["id"]}, headers=h).json()["items"]
    assert items, "should have exceptions on B"
    exc_id = items[0]["id"]

    viewer = _mk_user(client, h, f"authz-mut-viewer-{sfx}@x.com")  # global editor, viewer grant on B
    _grant(client, h, viewer["id"], b["id"], "viewer")
    editor = _mk_user(client, h, f"authz-mut-editor-{sfx}@x.com")
    _grant(client, h, editor["id"], b["id"], "editor")
    vh = _login(client, f"authz-mut-viewer-{sfx}@x.com")
    eh = _login(client, f"authz-mut-editor-{sfx}@x.com")

    # viewer-grant: can SEE the exception's events, but triage/comment are 403.
    assert client.get(f"{QH}/exceptions/{exc_id}/events", headers=vh).status_code == 200
    assert client.post(
        f"{QH}/exceptions/triage", json={"ids": [exc_id], "status": "acknowledged"}, headers=vh
    ).status_code == 403
    assert client.post(
        f"{QH}/exceptions/{exc_id}/comments", json={"comment": "nope"}, headers=vh
    ).status_code == 403
    # editor-grant: triage succeeds.
    assert client.post(
        f"{QH}/exceptions/triage", json={"ids": [exc_id], "status": "acknowledged"}, headers=eh
    ).status_code == 200

    assert client.delete(f"{QH}/connections/{b['id']}", headers=h).status_code == 204


def test_dashboard_scoped_to_grants(client, admin_headers, source_db):
    """The home dashboard/console aggregates are restricted to the caller's grants
    (#159): a user granted only an empty connection sees zero datasets, while admin
    sees the registered one."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"authz-dash-A-{sfx}", source_db)  # empty: no datasets
    b = _conn(client, h, f"authz-dash-B-{sfx}", source_db)
    _register_people(client, h, b["id"])
    u = _mk_user(client, h, f"authz-dash-u-{sfx}@x.com")
    _grant(client, h, u["id"], a["id"], "editor")  # granted only on the empty connection
    uh = _login(client, f"authz-dash-u-{sfx}@x.com")

    assert client.get(f"{QH}/dashboard", headers=uh).json()["datasets"] == 0
    assert client.get(f"{QH}/dashboard", headers=h).json()["datasets"] >= 1
    assert client.get(f"{QH}/dashboard/console", headers=uh).json()["open_total"] == 0

    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204
