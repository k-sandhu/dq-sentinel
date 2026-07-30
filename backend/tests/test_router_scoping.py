"""Router-level object authorization + LIKE-wildcard escaping (#72, #282/#273).

Five routers used to reach a source by `connection_id` / `dataset_id` with only a
GLOBAL role check: saved-query run, ad-hoc dashboards, custom-dashboard SQL
widgets, lineage/DDL introspection, and data contracts. They now go through the
same helpers as `POST /query/run` (`app/security.py`), so this module asserts the
three-way contract those helpers define:

  * **404** for missing OR invisible — with an IDENTICAL body, so ids can't be
    probed;
  * **403** only when the connection IS visible but the grant is viewer-only;
  * a **zero-grant user keeps full legacy access** — most deployments have no
    grants at all and everything must keep working.

Plus: a `?q=` containing `%` / `_` must search for that character instead of
turning into a match-everything wildcard.

Connection/user names include `uuid4().hex` because the app DB is shared across
the whole test session and `Connection.name` is unique.
"""

from uuid import uuid4

QH = "/api/v1"


# ---------------------------------------------------------------- fixtures ----
def _mk_user(client, admin_headers, email, role="editor"):
    r = client.post(
        f"{QH}/auth/users",
        json={"email": email, "name": email, "password": "password1", "role": role},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


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
    r = client.post(
        f"{QH}/connections", json={"name": name, "dsn": source_db}, headers=admin_headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def _register_people(client, admin_headers, connection_id):
    r = client.post(
        f"{QH}/datasets/register",
        json={"connection_id": connection_id, "tables": [{"table_name": "people"}]},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()[0]


def _saved_query(client, headers, connection_id, name, sql="SELECT 1 AS n"):
    r = client.post(
        f"{QH}/queries",
        json={"connection_id": connection_id, "name": name, "sql": sql},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _adhoc_dashboard(client, admin_headers, dataset_id):
    assert client.post(f"{QH}/datasets/{dataset_id}/profile", headers=admin_headers).status_code == 200
    r = client.post(
        f"{QH}/adhoc-dashboards/generate", json={"dataset_id": dataset_id}, headers=admin_headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def _sql_widget(connection_id, sql="SELECT COUNT(*) AS n FROM people"):
    return {
        "id": uuid4().hex,
        "title": "rows",
        "span": 2,
        "type": "sql",
        "config": {
            "connection_id": connection_id,
            "sql": sql,
            "viz": {"type": "number", "x": None, "y": "n"},
        },
    }


# --------------------------------------------------- saved queries (#72) ------
def test_saved_query_run_and_reads_scoped_to_grants(client, admin_headers, source_db):
    """Running a saved query executes persisted SQL against its connection, so it
    needs editor ON THAT connection — not merely the global editor role."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"rs-sq-A-{sfx}", source_db)
    b = _conn(client, h, f"rs-sq-B-{sfx}", source_db)
    q_b = _saved_query(client, h, b["id"], f"rs-sq-onB-{sfx}")
    q_a = _saved_query(client, h, a["id"], f"rs-sq-onA-{sfx}")

    alice = _mk_user(client, h, f"rs-sq-alice-{sfx}@x.com")  # global editor, granted A only
    _grant(client, h, alice["id"], a["id"], "editor")
    ah = _login(client, f"rs-sq-alice-{sfx}@x.com")
    carol = _mk_user(client, h, f"rs-sq-carol-{sfx}@x.com")  # global editor, VIEWER grant on A
    _grant(client, h, carol["id"], a["id"], "viewer")
    ch = _login(client, f"rs-sq-carol-{sfx}@x.com")

    # Ungranted connection: run is 404 and INDISTINGUISHABLE from a missing id.
    missing = client.post(f"{QH}/queries/999999999/run", headers=ah)
    invisible = client.post(f"{QH}/queries/{q_b['id']}/run", headers=ah)
    assert missing.status_code == invisible.status_code == 404
    assert missing.json()["detail"] == invisible.json()["detail"]
    # ...and the by-id read leaks nothing either (the response carries the SQL).
    assert client.get(f"{QH}/queries/{q_b['id']}", headers=ah).status_code == 404
    # Granted editor: the same call succeeds on A.
    ok = client.post(f"{QH}/queries/{q_a['id']}/run", headers=ah)
    assert ok.status_code == 200, ok.text

    # Visible but viewer-granted -> 403 (not 404): existence is already known.
    assert client.get(f"{QH}/queries/{q_a['id']}", headers=ch).status_code == 200
    assert client.post(f"{QH}/queries/{q_a['id']}/run", headers=ch).status_code == 403

    # List is scoped: A's query is listed for alice, B's never is.
    listed = {item["id"] for item in client.get(f"{QH}/queries", headers=ah).json()}
    assert q_a["id"] in listed and q_b["id"] not in listed
    admin_listed = {item["id"] for item in client.get(f"{QH}/queries", headers=h).json()}
    assert {q_a["id"], q_b["id"]} <= admin_listed

    for qid in (q_a["id"], q_b["id"]):
        assert client.delete(f"{QH}/queries/{qid}", headers=h).status_code == 204
    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


def test_saved_query_zero_grant_user_keeps_legacy_access(client, admin_headers, source_db):
    """Regression guard: deployments with NO grants must be untouched by #72 —
    a global editor with zero grants still reads, lists and runs everything."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    c = _conn(client, h, f"rs-sq-legacy-{sfx}", source_db)
    q = _saved_query(client, h, c["id"], f"rs-sq-legacy-q-{sfx}")

    _mk_user(client, h, f"rs-sq-nory-{sfx}@x.com")  # global editor, NO grants
    nh = _login(client, f"rs-sq-nory-{sfx}@x.com")

    assert client.get(f"{QH}/queries/{q['id']}", headers=nh).status_code == 200
    assert q["id"] in {item["id"] for item in client.get(f"{QH}/queries", headers=nh).json()}
    assert client.post(f"{QH}/queries/{q['id']}/run", headers=nh).status_code == 200

    assert client.delete(f"{QH}/queries/{q['id']}", headers=h).status_code == 204
    assert client.delete(f"{QH}/connections/{c['id']}", headers=h).status_code == 204


def test_saved_query_global_viewer_cannot_run(client, admin_headers, source_db):
    """A global viewer can browse the shared library but never execute from it —
    the effective role is capped by the global role (matches /query/run)."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    c = _conn(client, h, f"rs-sq-viewer-{sfx}", source_db)
    q = _saved_query(client, h, c["id"], f"rs-sq-viewer-q-{sfx}")

    _mk_user(client, h, f"rs-sq-vw-{sfx}@x.com", role="viewer")  # zero grants
    vh = _login(client, f"rs-sq-vw-{sfx}@x.com")

    assert client.get(f"{QH}/queries/{q['id']}", headers=vh).status_code == 200
    assert client.post(f"{QH}/queries/{q['id']}/run", headers=vh).status_code == 403

    assert client.delete(f"{QH}/queries/{q['id']}", headers=h).status_code == 204
    assert client.delete(f"{QH}/connections/{c['id']}", headers=h).status_code == 204


# ------------------------------------------------ ad-hoc dashboards (#72) -----
def test_adhoc_dashboard_open_scoped_to_grants(client, admin_headers, source_db):
    """Opening an ad-hoc board RE-EXECUTES its persisted panel SQL, so despite
    being a GET it carries /query/run's gate: editor on the board's connection."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"rs-ad-A-{sfx}", source_db)
    b = _conn(client, h, f"rs-ad-B-{sfx}", source_db)
    ds_b = _register_people(client, h, b["id"])
    dash_b = _adhoc_dashboard(client, h, ds_b["id"])

    alice = _mk_user(client, h, f"rs-ad-alice-{sfx}@x.com")  # global editor, granted A only
    _grant(client, h, alice["id"], a["id"], "editor")
    ah = _login(client, f"rs-ad-alice-{sfx}@x.com")

    # 404, identical to a missing id — a dashboard id must not be probeable.
    missing = client.get(f"{QH}/adhoc-dashboards/999999999", headers=ah)
    invisible = client.get(f"{QH}/adhoc-dashboards/{dash_b['id']}", headers=ah)
    assert missing.status_code == invisible.status_code == 404
    assert missing.json()["detail"] == invisible.json()["detail"]
    # generate against an ungranted dataset is 404 too (it executes SQL immediately)
    assert client.post(
        f"{QH}/adhoc-dashboards/generate", json={"dataset_id": ds_b["id"]}, headers=ah
    ).status_code == 404
    # list never surfaces B's board
    assert dash_b["id"] not in {
        m["id"] for m in client.get(f"{QH}/adhoc-dashboards", headers=ah).json()
    }
    # admin still sees + opens it
    assert client.get(f"{QH}/adhoc-dashboards/{dash_b['id']}", headers=h).status_code == 200
    assert dash_b["id"] in {
        m["id"]
        for m in client.get(
            f"{QH}/adhoc-dashboards", params={"dataset_id": ds_b["id"]}, headers=h
        ).json()
    }

    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


def test_adhoc_dashboard_viewer_grant_cannot_execute(client, admin_headers, source_db):
    """Visible but viewer-granted -> 403, and a zero-grant editor still opens it
    (legacy full access preserved)."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    c = _conn(client, h, f"rs-adv-{sfx}", source_db)
    ds = _register_people(client, h, c["id"])
    dash = _adhoc_dashboard(client, h, ds["id"])

    carol = _mk_user(client, h, f"rs-adv-carol-{sfx}@x.com")  # global editor, viewer grant
    _grant(client, h, carol["id"], c["id"], "viewer")
    ch = _login(client, f"rs-adv-carol-{sfx}@x.com")
    _mk_user(client, h, f"rs-adv-nory-{sfx}@x.com")  # global editor, NO grants
    nh = _login(client, f"rs-adv-nory-{sfx}@x.com")

    # the board is visible to carol (it appears in her list) but she can't execute it
    assert dash["id"] in {m["id"] for m in client.get(f"{QH}/adhoc-dashboards", headers=ch).json()}
    assert client.get(f"{QH}/adhoc-dashboards/{dash['id']}", headers=ch).status_code == 403
    # zero-grant regression guard
    assert client.get(f"{QH}/adhoc-dashboards/{dash['id']}", headers=nh).status_code == 200

    assert client.delete(f"{QH}/connections/{c['id']}", headers=h).status_code == 204


# ------------------------------------------- custom-dashboard SQL widgets -----
def test_custom_dashboard_sql_widget_refresh_scoped_to_grants(client, admin_headers, source_db):
    """A stored sql widget must not become a way to run SQL on an ungranted
    source: /refresh checks editor per widget, and saving a widget pointed at an
    invisible connection is refused with the same 422 as a nonexistent one."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"rs-cd-A-{sfx}", source_db)
    b = _conn(client, h, f"rs-cd-B-{sfx}", source_db)

    alice = _mk_user(client, h, f"rs-cd-alice-{sfx}@x.com")  # global editor, granted A only
    _grant(client, h, alice["id"], a["id"], "editor")
    ah = _login(client, f"rs-cd-alice-{sfx}@x.com")

    # Save-time: pointing a NEW widget at B is refused, and the message is the same
    # one a nonexistent connection gets — a save must not probe connection ids.
    on_b = client.post(
        f"{QH}/dashboards/custom",
        json={"name": f"rs-cd-b-{sfx}", "layout": {"version": 1, "widgets": [_sql_widget(b["id"])]}},
        headers=ah,
    )
    on_missing = client.post(
        f"{QH}/dashboards/custom",
        json={"name": f"rs-cd-m-{sfx}", "layout": {"version": 1, "widgets": [_sql_widget(999999999)]}},
        headers=ah,
    )
    assert on_b.status_code == on_missing.status_code == 422
    assert "not found" in on_b.text.lower() and "not found" in on_missing.text.lower()

    # A board admin built on B, shared with the team: alice may open it, but the
    # widget can neither execute nor hand her the rows an admin captured.
    admin_board = client.post(
        f"{QH}/dashboards/custom",
        json={
            "name": f"rs-cd-team-{sfx}",
            "visibility": "team",
            "layout": {"version": 1, "widgets": [_sql_widget(b["id"])]},
        },
        headers=h,
    )
    assert admin_board.status_code == 201, admin_board.text
    did = admin_board.json()["id"]
    refreshed = client.post(f"{QH}/dashboards/custom/{did}/refresh", headers=h).json()
    assert refreshed["layout"]["widgets"][0]["snapshot"]["rows"][0][0] == 200

    seen = client.get(f"{QH}/dashboards/custom/{did}", headers=ah).json()
    snap = seen["layout"]["widgets"][0]["snapshot"]
    assert snap["rows"] == [] and snap["columns"] == []  # redacted for an ungranted viewer
    assert "access" in (snap["error"] or "").lower()

    # alice refreshing does NOT execute on B, and does NOT clobber the admin's rows.
    alice_refresh = client.post(f"{QH}/dashboards/custom/{did}/refresh", headers=ah)
    assert alice_refresh.status_code == 200, alice_refresh.text
    assert alice_refresh.json()["layout"]["widgets"][0]["snapshot"]["rows"] == []
    still = client.get(f"{QH}/dashboards/custom/{did}", headers=h).json()
    assert still["layout"]["widgets"][0]["snapshot"]["rows"][0][0] == 200

    assert client.delete(f"{QH}/dashboards/custom/{did}", headers=h).status_code == 204
    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


def test_custom_dashboard_zero_grant_refresh_still_works(client, admin_headers, source_db):
    """Regression guard for the legacy (no grants anywhere) deployment."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    c = _conn(client, h, f"rs-cd-legacy-{sfx}", source_db)
    _register_people(client, h, c["id"])
    _mk_user(client, h, f"rs-cd-nory-{sfx}@x.com")  # global editor, NO grants
    nh = _login(client, f"rs-cd-nory-{sfx}@x.com")

    did = client.post(
        f"{QH}/dashboards/custom",
        json={"name": f"rs-cd-legacy-b-{sfx}", "layout": {"version": 1, "widgets": [_sql_widget(c["id"])]}},
        headers=nh,
    ).json()["id"]
    out = client.post(f"{QH}/dashboards/custom/{did}/refresh", headers=nh)
    assert out.status_code == 200, out.text
    snap = out.json()["layout"]["widgets"][0]["snapshot"]
    assert snap["error"] is None
    assert snap["rows"][0][0] == 200

    assert client.delete(f"{QH}/dashboards/custom/{did}", headers=nh).status_code == 204
    assert client.delete(f"{QH}/connections/{c['id']}", headers=h).status_code == 204


# ----------------------------------------------------------- lineage (#72) ----
def test_lineage_and_ddl_scoped_to_grants(client, admin_headers, source_db):
    """DDL + lineage describe the SOURCE schema, so they are grant-gated reads:
    404 for an ungranted dataset/connection, identical to a missing id."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"rs-lin-A-{sfx}", source_db)
    b = _conn(client, h, f"rs-lin-B-{sfx}", source_db)
    ds_b = _register_people(client, h, b["id"])

    alice = _mk_user(client, h, f"rs-lin-alice-{sfx}@x.com")  # granted A only
    _grant(client, h, alice["id"], a["id"], "editor")
    ah = _login(client, f"rs-lin-alice-{sfx}@x.com")
    _mk_user(client, h, f"rs-lin-nory-{sfx}@x.com")  # zero grants -> legacy full access
    nh = _login(client, f"rs-lin-nory-{sfx}@x.com")

    # Templates (not str.replace) so a low dataset id can't rewrite "/api/v1/".
    for template in (
        QH + "/datasets/{}/ddl",
        QH + "/datasets/{}/lineage",
        QH + "/datasets/{}/lineage/columns?column=email",
    ):
        path = template.format(ds_b["id"])
        invisible = client.get(path, headers=ah)
        assert invisible.status_code == 404, path
        missing = client.get(template.format(999999999), headers=ah)
        assert missing.status_code == 404
        assert missing.json()["detail"] == invisible.json()["detail"]
        assert client.get(path, headers=h).status_code == 200, path
        assert client.get(path, headers=nh).status_code == 200, path  # zero-grant legacy

    conn_lineage = f"{QH}/connections/{b['id']}/lineage"
    invisible = client.get(conn_lineage, headers=ah)
    missing = client.get(f"{QH}/connections/999999999/lineage", headers=ah)
    assert invisible.status_code == missing.status_code == 404
    assert invisible.json()["detail"] == missing.json()["detail"]
    assert client.get(conn_lineage, headers=h).status_code == 200
    assert client.get(conn_lineage, headers=nh).status_code == 200

    # A viewer grant is enough to READ lineage (it introspects, it never mutates).
    carol = _mk_user(client, h, f"rs-lin-carol-{sfx}@x.com")
    _grant(client, h, carol["id"], b["id"], "viewer")
    ch = _login(client, f"rs-lin-carol-{sfx}@x.com")
    assert client.get(f"{QH}/datasets/{ds_b['id']}/ddl", headers=ch).status_code == 200

    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


# --------------------------------------------------------- contracts (#72) ----
CONTRACT_SPEC = {
    "schema": {
        "columns": [{"name": "id", "dtype": "INTEGER", "required": True}],
        "allow_extra_columns": True,
    }
}


def _contract(client, headers, dataset_id, name, spec=CONTRACT_SPEC, version="1.0.0"):
    r = client.post(
        f"{QH}/datasets/{dataset_id}/contract",
        json={"name": name, "version": version, "spec": spec},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_contract_reads_scoped_to_grants(client, admin_headers, source_db):
    """A contract read describes the SOURCE schema (`default_contract_spec` and
    `conformance` introspect the live table), so every read is grant-gated: 404
    for an ungranted dataset, identical to a missing id."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"rs-ct-A-{sfx}", source_db)
    b = _conn(client, h, f"rs-ct-B-{sfx}", source_db)
    ds_b = _register_people(client, h, b["id"])
    contract = _contract(client, h, ds_b["id"], f"rs-ct-{sfx}")

    alice = _mk_user(client, h, f"rs-ct-alice-{sfx}@x.com")  # granted A only
    _grant(client, h, alice["id"], a["id"], "editor")
    ah = _login(client, f"rs-ct-alice-{sfx}@x.com")
    _mk_user(client, h, f"rs-ct-nory-{sfx}@x.com")  # zero grants -> legacy full access
    nh = _login(client, f"rs-ct-nory-{sfx}@x.com")

    # Templates (not str.replace) so a low dataset id can't rewrite "/api/v1/".
    for template in (
        QH + "/datasets/{}/contracts",
        QH + "/datasets/{}/contract",
        QH + "/datasets/{}/contract/conformance",
        QH + "/datasets/{}/contract/export",
        QH + "/datasets/{}/contract/" + str(contract["id"]),
        QH + "/datasets/{}/contract/" + str(contract["id"]) + "/conformance",
        QH + "/datasets/{}/contract/" + str(contract["id"]) + "/versions",
        QH + "/datasets/{}/contract/" + str(contract["id"]) + "/export",
    ):
        path = template.format(ds_b["id"])
        invisible = client.get(path, headers=ah)
        assert invisible.status_code == 404, path
        missing = client.get(template.format(999999999), headers=ah)
        assert missing.status_code == 404, path
        assert missing.json()["detail"] == invisible.json()["detail"], path
        assert client.get(path, headers=h).status_code == 200, path
        assert client.get(path, headers=nh).status_code == 200, path  # zero-grant legacy

    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


def test_contract_writes_scoped_to_grants(client, admin_headers, source_db):
    """Contract writes are the sharp end: creating with no spec introspects the
    live source, and activating MATERIALIZES checks the scheduler then executes
    against that connection. Both need editor ON the dataset's connection —
    404 when it is invisible, 403 when it is visible but viewer-granted."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"rs-ctw-A-{sfx}", source_db)
    b = _conn(client, h, f"rs-ctw-B-{sfx}", source_db)
    ds_b = _register_people(client, h, b["id"])
    contract = _contract(client, h, ds_b["id"], f"rs-ctw-{sfx}")

    alice = _mk_user(client, h, f"rs-ctw-alice-{sfx}@x.com")  # global editor, granted A only
    _grant(client, h, alice["id"], a["id"], "editor")
    ah = _login(client, f"rs-ctw-alice-{sfx}@x.com")

    # No "spec" on purpose: default_contract_spec would reach B's live source and
    # hand back its column list (dtypes + nullability) if this weren't gated.
    create = client.post(f"{QH}/datasets/{ds_b['id']}/contract", json={"name": "x"}, headers=ah)
    missing = client.post(f"{QH}/datasets/999999999/contract", json={"name": "x"}, headers=ah)
    assert create.status_code == missing.status_code == 404, create.text
    assert create.json()["detail"] == missing.json()["detail"]

    cpath = f"{QH}/datasets/{ds_b['id']}/contract/{contract['id']}"
    for resp in (
        client.post(f"{cpath}/activate", headers=ah),
        client.patch(cpath, json={"name": "hijacked"}, headers=ah),
        client.delete(cpath, headers=ah),
        client.post(
            f"{QH}/datasets/{ds_b['id']}/contract/import",
            json={"yaml": "kind: DataContract\nname: x\nversion: 1.0.0\n"},
            headers=ah,
        ),
    ):
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == missing.json()["detail"]

    # Nothing was materialized on B, and the contract is untouched.
    assert client.get(f"{QH}/checks", params={"dataset_id": ds_b["id"]}, headers=h).json() == []
    still = client.get(cpath, headers=h).json()
    assert still["name"] == f"rs-ctw-{sfx}" and still["status"] == "draft"
    assert len(client.get(f"{QH}/datasets/{ds_b['id']}/contracts", headers=h).json()) == 1

    # Visible but viewer-granted -> 403 (not 404): existence is already known.
    carol = _mk_user(client, h, f"rs-ctw-carol-{sfx}@x.com")  # global editor, VIEWER grant on B
    _grant(client, h, carol["id"], b["id"], "viewer")
    ch = _login(client, f"rs-ctw-carol-{sfx}@x.com")
    assert client.get(cpath, headers=ch).status_code == 200  # reading is fine
    assert client.post(f"{cpath}/activate", headers=ch).status_code == 403
    assert client.post(
        f"{QH}/datasets/{ds_b['id']}/contract", json={"name": "x"}, headers=ch
    ).status_code == 403
    assert client.patch(cpath, json={"name": "hijacked"}, headers=ch).status_code == 403
    assert client.delete(cpath, headers=ch).status_code == 403

    # Zero-grant editor keeps full legacy access (most deployments have no grants).
    _mk_user(client, h, f"rs-ctw-nory-{sfx}@x.com")
    nh = _login(client, f"rs-ctw-nory-{sfx}@x.com")
    legacy = _contract(client, h, ds_b["id"], f"rs-ctw-legacy-{sfx}", version="2.0.0")
    assert client.patch(
        f"{QH}/datasets/{ds_b['id']}/contract/{legacy['id']}",
        json={"name": f"rs-ctw-legacy-renamed-{sfx}"},
        headers=nh,
    ).status_code == 200
    activated = client.post(
        f"{QH}/datasets/{ds_b['id']}/contract/{legacy['id']}/activate", headers=nh
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["created_checks"]

    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


# ------------------------------------------------------------ checks (#72) ----
def _check(client, headers, dataset_id, name, column="id"):
    r = client.post(
        f"{QH}/checks",
        json={
            "dataset_id": dataset_id,
            "check_type": "not_null",
            "column_name": column,
            "name": name,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_check_writes_scoped_to_grants(client, admin_headers, source_db):
    """Authoring a check decides what the scheduler will EXECUTE against a source,
    and `POST /checks/{id}/run` executes it immediately, so create/generate/patch/
    run/archive all need editor ON the dataset's connection — not just the global
    editor role. Missing and invisible are indistinguishable (identical 404 body)."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"rs-ck-A-{sfx}", source_db)
    b = _conn(client, h, f"rs-ck-B-{sfx}", source_db)
    ds_b = _register_people(client, h, b["id"])
    chk_b = _check(client, h, ds_b["id"], f"rs-ck-onB-{sfx}")

    alice = _mk_user(client, h, f"rs-ck-alice-{sfx}@x.com")  # global editor, granted A only
    _grant(client, h, alice["id"], a["id"], "editor")
    ah = _login(client, f"rs-ck-alice-{sfx}@x.com")

    # create/generate keyed by dataset_id: 404, identical to a nonexistent dataset.
    ds_missing = client.post(
        f"{QH}/checks",
        json={"dataset_id": 999999999, "check_type": "not_null", "column_name": "id"},
        headers=ah,
    )
    assert ds_missing.status_code == 404, ds_missing.text
    for resp in (
        client.post(
            f"{QH}/checks",
            json={"dataset_id": ds_b["id"], "check_type": "not_null", "column_name": "id"},
            headers=ah,
        ),
        client.post(f"{QH}/checks/generate", json={"dataset_id": ds_b["id"]}, headers=ah),
    ):
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == ds_missing.json()["detail"]

    # check-keyed mutations: 404, identical to a nonexistent check id.
    chk_missing = client.patch(f"{QH}/checks/999999999", json={"name": "x"}, headers=ah)
    assert chk_missing.status_code == 404, chk_missing.text
    cpath = f"{QH}/checks/{chk_b['id']}"
    for resp in (
        client.patch(cpath, json={"name": f"rs-ck-hijacked-{sfx}"}, headers=ah),
        client.post(f"{cpath}/run", headers=ah),
        client.delete(cpath, headers=ah),
    ):
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == chk_missing.json()["detail"]

    # Nothing on B changed: no rename, still active, and no run was executed.
    still = client.get(cpath, headers=h).json()
    assert still["name"] == f"rs-ck-onB-{sfx}" and still["status"] == "active"
    assert client.get(f"{QH}/runs", params={"check_id": chk_b["id"]}, headers=h).json() == []
    # ...and the list never enumerates B's checks for alice.
    assert client.get(f"{QH}/checks", params={"dataset_id": ds_b["id"]}, headers=ah).json() == []
    assert chk_b["id"] not in {c["id"] for c in client.get(f"{QH}/checks", headers=ah).json()}
    assert chk_b["id"] in {c["id"] for c in client.get(f"{QH}/checks", headers=h).json()}

    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


def test_check_viewer_grant_cannot_author_or_run(client, admin_headers, source_db):
    """Visible but viewer-granted -> 403 (existence is already known), and a
    zero-grant editor keeps full legacy authoring/run access."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    b = _conn(client, h, f"rs-ckv-B-{sfx}", source_db)
    ds_b = _register_people(client, h, b["id"])
    chk_b = _check(client, h, ds_b["id"], f"rs-ckv-onB-{sfx}")
    cpath = f"{QH}/checks/{chk_b['id']}"

    carol = _mk_user(client, h, f"rs-ckv-carol-{sfx}@x.com")  # global editor, VIEWER grant on B
    _grant(client, h, carol["id"], b["id"], "viewer")
    ch = _login(client, f"rs-ckv-carol-{sfx}@x.com")

    assert client.get(cpath, headers=ch).status_code == 200  # reading is fine
    assert client.post(
        f"{QH}/checks",
        json={"dataset_id": ds_b["id"], "check_type": "not_null", "column_name": "id"},
        headers=ch,
    ).status_code == 403
    assert client.post(
        f"{QH}/checks/generate", json={"dataset_id": ds_b["id"]}, headers=ch
    ).status_code == 403
    assert client.patch(cpath, json={"name": "hijacked"}, headers=ch).status_code == 403
    assert client.post(f"{cpath}/run", headers=ch).status_code == 403
    assert client.delete(cpath, headers=ch).status_code == 403

    # Zero-grant editor: unchanged legacy behavior end to end.
    _mk_user(client, h, f"rs-ckv-nory-{sfx}@x.com")
    nh = _login(client, f"rs-ckv-nory-{sfx}@x.com")
    legacy = _check(client, nh, ds_b["id"], f"rs-ckv-legacy-{sfx}", column="status")
    lpath = f"{QH}/checks/{legacy['id']}"
    assert client.patch(lpath, json={"severity": "warn"}, headers=nh).status_code == 200
    ran = client.post(f"{lpath}/run", headers=nh)
    assert ran.status_code == 200, ran.text
    # generate has no profile yet -> 409 from the endpoint's own precondition, which
    # proves the grant gate let a zero-grant editor through.
    assert client.post(
        f"{QH}/checks/generate", json={"dataset_id": ds_b["id"]}, headers=nh
    ).status_code == 409
    assert client.delete(lpath, headers=nh).status_code == 204

    assert client.delete(f"{QH}/connections/{b['id']}", headers=h).status_code == 204


# ---------------------------------------------------- monitor packs (#72) -----
def test_monitor_pack_scoped_to_grants(client, admin_headers, source_db):
    """A monitor pack materializes + schedules checks against the dataset's SOURCE,
    and disabling it silently stops all monitoring — so reads are grant-gated and
    every mutation needs editor ON the connection."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"rs-mp-A-{sfx}", source_db)
    b = _conn(client, h, f"rs-mp-B-{sfx}", source_db)
    ds_b = _register_people(client, h, b["id"])

    alice = _mk_user(client, h, f"rs-mp-alice-{sfx}@x.com")  # global editor, granted A only
    _grant(client, h, alice["id"], a["id"], "editor")
    ah = _login(client, f"rs-mp-alice-{sfx}@x.com")

    base = f"{QH}/datasets/{ds_b['id']}/monitor-pack"
    missing = client.get(f"{QH}/datasets/999999999/monitor-pack", headers=ah)
    assert missing.status_code == 404, missing.text
    for resp in (
        client.get(base, headers=ah),
        client.patch(base, json={"enabled": False}, headers=ah),
        client.post(f"{base}/reconcile", headers=ah),
    ):
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == missing.json()["detail"]

    # Monitoring on B was NOT silently disabled.
    assert client.get(base, headers=h).json()["enabled"] is True

    # Visible but viewer-granted: read yes, mutate no.
    carol = _mk_user(client, h, f"rs-mp-carol-{sfx}@x.com")
    _grant(client, h, carol["id"], b["id"], "viewer")
    ch = _login(client, f"rs-mp-carol-{sfx}@x.com")
    assert client.get(base, headers=ch).status_code == 200
    assert client.patch(base, json={"enabled": False}, headers=ch).status_code == 403
    assert client.post(f"{base}/reconcile", headers=ch).status_code == 403
    assert client.get(base, headers=h).json()["enabled"] is True

    # Zero-grant editor keeps full legacy access.
    _mk_user(client, h, f"rs-mp-nory-{sfx}@x.com")
    nh = _login(client, f"rs-mp-nory-{sfx}@x.com")
    assert client.get(base, headers=nh).status_code == 200
    assert client.patch(base, json={"enabled": True}, headers=nh).status_code == 200
    assert client.post(f"{base}/reconcile", headers=nh).status_code == 200

    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


# ------------------------------------------------------- RCA sessions (#72) ---
def _rca_session(dataset_id: int, question: str) -> int:
    """Insert a completed RCA session directly (starting one needs an LLM)."""
    from app.db import session_factory
    from app.models import RcaSession

    with session_factory()() as db:
        s = RcaSession(
            dataset_id=dataset_id,
            question=question,
            status="complete",
            report_md="rows from someone else's source",
            transcript=[{"type": "sql", "content": "SELECT email FROM people"}],
            report_json={"version": 1, "likely_cause": "leaked evidence"},
        )
        db.add(s)
        db.commit()
        return s.id


def test_rca_sessions_scoped_to_grants(client, admin_headers, source_db):
    """An RCA session carries the agent's SQL transcript plus `report_json` evidence
    — rows and schema from the dataset's source — so reads are grant-gated and
    starting one (the agent writes SQL against the source) needs editor there."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    a = _conn(client, h, f"rs-rca-A-{sfx}", source_db)
    b = _conn(client, h, f"rs-rca-B-{sfx}", source_db)
    ds_b = _register_people(client, h, b["id"])
    sid = _rca_session(ds_b["id"], f"rs-rca-q-{sfx}")

    alice = _mk_user(client, h, f"rs-rca-alice-{sfx}@x.com")  # global editor, granted A only
    _grant(client, h, alice["id"], a["id"], "editor")
    ah = _login(client, f"rs-rca-alice-{sfx}@x.com")

    # by-id read: 404, identical to a nonexistent session id.
    missing = client.get(f"{QH}/rca/999999999", headers=ah)
    invisible = client.get(f"{QH}/rca/{sid}", headers=ah)
    assert missing.status_code == invisible.status_code == 404, invisible.text
    assert missing.json()["detail"] == invisible.json()["detail"]
    # list: B's session never appears, admin's does.
    assert client.get(f"{QH}/rca", params={"dataset_id": ds_b["id"]}, headers=ah).json() == []
    assert sid not in {s["id"] for s in client.get(f"{QH}/rca", headers=ah).json()}
    assert sid in {
        s["id"]
        for s in client.get(f"{QH}/rca", params={"dataset_id": ds_b["id"]}, headers=h).json()
    }
    # start: 404, identical to a nonexistent dataset (and BEFORE the LLM 503).
    ds_missing = client.post(
        f"{QH}/rca/start", json={"dataset_id": 999999999, "question": "probe"}, headers=ah
    )
    started = client.post(
        f"{QH}/rca/start", json={"dataset_id": ds_b["id"], "question": "probe"}, headers=ah
    )
    assert ds_missing.status_code == started.status_code == 404, started.text
    assert ds_missing.json()["detail"] == started.json()["detail"]

    # Visible but viewer-granted: read yes, start no.
    carol = _mk_user(client, h, f"rs-rca-carol-{sfx}@x.com")
    _grant(client, h, carol["id"], b["id"], "viewer")
    ch = _login(client, f"rs-rca-carol-{sfx}@x.com")
    assert client.get(f"{QH}/rca/{sid}", headers=ch).status_code == 200
    assert client.post(
        f"{QH}/rca/start", json={"dataset_id": ds_b["id"], "question": "probe"}, headers=ch
    ).status_code == 403

    # Zero-grant editor keeps full legacy access: reads the session, is listed, and
    # reaches the LLM precondition (503 here) instead of being blocked by the gate.
    _mk_user(client, h, f"rs-rca-nory-{sfx}@x.com")
    nh = _login(client, f"rs-rca-nory-{sfx}@x.com")
    assert client.get(f"{QH}/rca/{sid}", headers=nh).status_code == 200
    assert sid in {
        s["id"]
        for s in client.get(f"{QH}/rca", params={"dataset_id": ds_b["id"]}, headers=nh).json()
    }
    assert client.post(
        f"{QH}/rca/start", json={"dataset_id": ds_b["id"], "question": "probe"}, headers=nh
    ).status_code == 503

    for cid in (a["id"], b["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


# ------------------------------------------- LIKE wildcard escaping (#282) ----
def test_q_filter_escapes_like_wildcards_saved_queries(client, admin_headers, source_db):
    """`?q=%` must search for a literal percent sign, not match every row."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    c = _conn(client, h, f"rs-esc-sq-{sfx}", source_db)
    literal = _saved_query(client, h, c["id"], f"rs-esc-100%-share-{sfx}")
    plain = _saved_query(client, h, c["id"], f"rs-esc-plain-{sfx}")
    under = _saved_query(client, h, c["id"], f"rs-esc_under-{sfx}")

    def ids(q):
        return {
            item["id"]
            for item in client.get(
                f"{QH}/queries", params={"connection_id": c["id"], "q": q}, headers=h
            ).json()
        }

    # "%" alone would match everything if it stayed a wildcard.
    pct = ids("%")
    assert literal["id"] in pct
    assert plain["id"] not in pct and under["id"] not in pct
    # "_" is the single-character wildcard; escaped it matches only the underscore.
    us = ids("rs-esc_under")
    assert under["id"] in us
    assert plain["id"] not in us and literal["id"] not in us
    # a plain needle still works
    assert plain["id"] in ids("rs-esc-plain")

    for q in (literal, plain, under):
        assert client.delete(f"{QH}/queries/{q['id']}", headers=h).status_code == 204
    assert client.delete(f"{QH}/connections/{c['id']}", headers=h).status_code == 204


def test_q_filter_escapes_like_wildcards_exceptions(client, admin_headers, source_db):
    """Same for the triage queue's search box, which filters on the check name."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    c = _conn(client, h, f"rs-esc-exc-{sfx}", source_db)
    ds = _register_people(client, h, c["id"])

    made = {}
    for label in (f"rs-exc-100%-null-{sfx}", f"rs-exc-plain-{sfx}"):
        chk = client.post(
            f"{QH}/checks",
            json={
                "dataset_id": ds["id"],
                "check_type": "not_null",
                "column_name": "email",
                "name": label,
            },
            headers=h,
        )
        assert chk.status_code == 201, chk.text
        assert client.post(f"{QH}/checks/{chk.json()['id']}/run", headers=h).status_code in (200, 201)
        made[label] = chk.json()["id"]

    def check_ids(q):
        body = client.get(
            f"{QH}/exceptions", params={"dataset_id": ds["id"], "q": q}, headers=h
        ).json()
        return {item["check_id"] for item in body["items"]}, body["total"]

    unfiltered_total = client.get(
        f"{QH}/exceptions", params={"dataset_id": ds["id"]}, headers=h
    ).json()["total"]
    assert unfiltered_total > 0

    hits, total = check_ids("%")
    assert total < unfiltered_total  # "%" is no longer a match-everything wildcard
    assert made[f"rs-exc-100%-null-{sfx}"] in hits
    assert made[f"rs-exc-plain-{sfx}"] not in hits

    # a literal needle is unaffected
    hits, _ = check_ids(f"rs-exc-plain-{sfx}")
    assert hits == {made[f"rs-exc-plain-{sfx}"]}

    assert client.delete(f"{QH}/connections/{c['id']}", headers=h).status_code == 204


def test_q_filter_escapes_like_wildcards_search_and_datasets(client, admin_headers, source_db):
    """cmd-K search and the datasets list share the same escaping helper (#273)."""
    h = admin_headers
    sfx = uuid4().hex[:8]
    c = _conn(client, h, f"rs-esc-search-100%-{sfx}", source_db)
    plain = _conn(client, h, f"rs-esc-search-plain-{sfx}", source_db)

    titles = {
        hit["title"]
        for hit in client.get(f"{QH}/search", params={"q": "%", "limit": 25}, headers=h).json()["hits"]
    }
    assert c["name"] in titles  # literal "%" match
    assert plain["name"] not in titles  # would be present if "%" were a wildcard

    # datasets ?q= uses the same helper: "_" matches literally, not any character.
    ds = _register_people(client, h, c["id"])
    named = {
        d["id"]
        for d in client.get(f"{QH}/datasets", params={"q": "p_ople"}, headers=h).json()
    }
    assert ds["id"] not in named  # "p_ople" would match "people" with a live wildcard
    assert ds["id"] in {
        d["id"] for d in client.get(f"{QH}/datasets", params={"q": "people"}, headers=h).json()
    }

    for cid in (c["id"], plain["id"]):
        assert client.delete(f"{QH}/connections/{cid}", headers=h).status_code == 204


def test_q_filter_escapes_like_wildcards_audit(client, admin_headers):
    """The audit viewer's `?q=` is a documented action PREFIX match. Escaping has
    to make a typed `_` literal WITHOUT widening the prefix into a substring
    search — an admin filtering the compliance trail must get exactly the actions
    they asked for."""
    from app.db import session_factory
    from app.models import AuditEntry

    sfx = uuid4().hex[:8]
    literal = f"rs_esc.{sfx}"  # a real underscore in the action name
    decoy = f"rsxesc.{sfx}"  # only matches "rs_esc." if "_" is still a wildcard
    factory = session_factory()
    with factory() as db:
        rows = [AuditEntry(action=literal, entity_type="test"), AuditEntry(action=decoy, entity_type="test")]
        db.add_all(rows)
        db.commit()
        made = {r.id for r in rows}

    def actions(q):
        body = client.get(
            f"{QH}/audit", params={"q": q, "limit": 200}, headers=admin_headers
        ).json()
        return {r["action"] for r in body["items"]}

    hits = actions(f"rs_esc.{sfx}")
    assert literal in hits
    assert decoy not in hits  # "_" matched literally, not "any character"
    assert decoy in actions(f"rsxesc.{sfx}")  # a plain needle still works
    # Prefix semantics preserved: the needle is NOT wrapped in a leading "%".
    assert actions(f"esc.{sfx}") == set()

    with factory() as db:
        for row in db.query(AuditEntry).filter(AuditEntry.id.in_(made)).all():
            db.delete(row)
        db.commit()
