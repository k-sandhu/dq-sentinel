"""Custom dashboards (issue #67): CRUD, ownership/RBAC matrix, widget validation,
SQL snapshot refresh, visibility.

The widget JSON shapes here are the authoritative contract (mirrored in
frontend/src/api/types.ts). metric/exceptions widgets resolve client-side through
GET /exceptions, so the backend only validates their stored params — it never
counts.
"""

import uuid

import pytest

API = "/api/v1/dashboards/custom"


def _wid() -> str:
    return uuid.uuid4().hex


@pytest.fixture(scope="module")
def editor_headers(client, admin_headers) -> dict[str, str]:
    client.post(
        "/api/v1/auth/users",
        json={"email": "cd-editor@example.com", "password": "editor123", "role": "editor"},
        headers=admin_headers,
    )
    resp = client.post(
        "/api/v1/auth/login", json={"email": "cd-editor@example.com", "password": "editor123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def viewer_headers(client, admin_headers) -> dict[str, str]:
    client.post(
        "/api/v1/auth/users",
        json={"email": "cd-viewer@example.com", "password": "viewer123", "role": "viewer"},
        headers=admin_headers,
    )
    resp = client.post(
        "/api/v1/auth/login", json={"email": "cd-viewer@example.com", "password": "viewer123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def source_conn_ds(client, admin_headers, source_db):
    """A registered connection + dataset against the synthetic source, for sql/checks widgets."""
    conn = client.post(
        "/api/v1/connections", json={"name": "cd-conn", "dsn": source_db}, headers=admin_headers
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=admin_headers,
    ).json()[0]
    return conn, ds


def _note_widget(title="Runbook") -> dict:
    return {"id": _wid(), "title": title, "span": 1, "type": "note",
            "config": {"markdown": "# Hi\n\n[runbook](http://x)"}}


def _metric_widget(params=None) -> dict:
    return {"id": _wid(), "title": "Open errors", "span": 1, "type": "metric",
            "config": {"params": params or {"status": "open"}, "warn_at": 5, "danger_at": 20}}


# ---------------------------------------------------------------- CRUD --------
def test_create_list_get_delete_minimal(client, viewer_headers):
    # A viewer can create a read-only (note + metric) dashboard.
    body = {"name": "My morning screen", "description": "daily",
            "layout": {"version": 1, "widgets": [_note_widget(), _metric_widget()]}}
    r = client.post(API, json=body, headers=viewer_headers)
    assert r.status_code == 201, r.text
    dash = r.json()
    assert dash["widget_count"] == 2
    assert dash["visibility"] == "private"
    assert dash["can_edit"] is True
    assert dash["owner_name"]  # joined display field present
    assert dash["layout"]["version"] == 1

    did = dash["id"]
    metas = client.get(API, headers=viewer_headers).json()
    assert any(m["id"] == did for m in metas)
    assert "layout" not in metas[0]  # list returns metas only (no layout)

    got = client.get(f"{API}/{did}", headers=viewer_headers).json()
    assert got["layout"]["widgets"][0]["type"] == "note"

    assert client.delete(f"{API}/{did}", headers=viewer_headers).status_code == 204
    assert client.get(f"{API}/{did}", headers=viewer_headers).status_code == 404


def test_patch_full_layout_replace(client, editor_headers):
    body = {"name": "edit-me", "layout": {"version": 1, "widgets": [_note_widget("first")]}}
    did = client.post(API, json=body, headers=editor_headers).json()["id"]

    # full replace + rename + visibility
    new = {"name": "renamed", "visibility": "team",
           "layout": {"version": 1, "widgets": [_note_widget("a"), _note_widget("b")]}}
    r = client.patch(f"{API}/{did}", json=new, headers=editor_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "renamed"
    assert out["visibility"] == "team"
    assert out["widget_count"] == 2


# ---------------------------------------------------- ownership / RBAC --------
def test_private_dashboard_404s_for_non_owner(client, editor_headers, viewer_headers):
    did = client.post(
        API, json={"name": "secret", "layout": {"version": 1, "widgets": [_note_widget()]}},
        headers=editor_headers,
    ).json()["id"]
    # non-owner: 404 (not 403) — don't leak existence of a private dashboard
    assert client.get(f"{API}/{did}", headers=viewer_headers).status_code == 404
    assert client.patch(f"{API}/{did}", json={"name": "x"}, headers=viewer_headers).status_code == 404
    assert client.delete(f"{API}/{did}", headers=viewer_headers).status_code == 404


def test_team_dashboard_visible_but_not_editable_by_others(client, editor_headers, viewer_headers):
    did = client.post(
        API, json={"name": "shared", "visibility": "team",
                   "layout": {"version": 1, "widgets": [_note_widget()]}},
        headers=editor_headers,
    ).json()["id"]
    # viewer (non-owner) can view a team dashboard read-only...
    got = client.get(f"{API}/{did}", headers=viewer_headers)
    assert got.status_code == 200
    assert got.json()["can_edit"] is False
    # ...but cannot edit it: team grants view, not edit authority (403, it's visible)
    assert client.patch(f"{API}/{did}", json={"name": "x"}, headers=viewer_headers).status_code == 403


def test_admin_overrides_edit_and_owner_reassign(client, admin_headers, editor_headers):
    me = client.get("/api/v1/auth/me", headers=admin_headers).json()
    did = client.post(
        API, json={"name": "owned-by-editor",
                   "layout": {"version": 1, "widgets": [_note_widget()]}},
        headers=editor_headers,
    ).json()["id"]
    # admin can edit someone else's private dashboard
    r = client.patch(f"{API}/{did}", json={"description": "by admin"}, headers=admin_headers)
    assert r.status_code == 200
    # admin can reassign ownership (offboarding) to the admin's own id
    good = client.patch(f"{API}/{did}", json={"owner_id": me["id"]}, headers=admin_headers)
    assert good.status_code == 200
    assert good.json()["owner_id"] == me["id"]


def test_owner_id_reassign_requires_admin(client, editor_headers):
    did = client.post(
        API, json={"name": "self-owned", "layout": {"version": 1, "widgets": [_note_widget()]}},
        headers=editor_headers,
    ).json()["id"]
    # owner (editor) editing their own board but trying to set owner_id -> 403 (admin-only field)
    r = client.patch(f"{API}/{did}", json={"owner_id": 999999}, headers=editor_headers)
    assert r.status_code == 403


# -------------------------------------------------------- duplicate ----------
def test_duplicate_makes_private_copy_clears_snapshots(client, editor_headers, viewer_headers, source_conn_ds):
    conn, _ds = source_conn_ds
    sqlw = {"id": _wid(), "title": "rows", "span": 2, "type": "sql",
            "config": {"connection_id": conn["id"], "sql": "SELECT COUNT(*) AS n FROM people",
                       "viz": {"type": "number", "x": None, "y": "n"}}}
    did = client.post(
        API, json={"name": "team-template", "visibility": "team",
                   "layout": {"version": 1, "widgets": [sqlw]}},
        headers=editor_headers,
    ).json()["id"]
    # populate a snapshot on the source
    client.post(f"{API}/{did}/refresh", headers=editor_headers)
    src = client.get(f"{API}/{did}", headers=editor_headers).json()
    assert src["layout"]["widgets"][0]["snapshot"]["rows"][0][0] == 200

    # a viewer who can see the team board duplicates it
    dup = client.post(f"{API}/{did}/duplicate", headers=viewer_headers)
    assert dup.status_code == 201, dup.text
    copy = dup.json()
    assert copy["visibility"] == "private"
    assert copy["owner_name"]  # owned by the caller now
    assert copy["id"] != did
    # snapshot cleared in the copy
    assert copy["layout"]["widgets"][0].get("snapshot") in (None, {})


# ----------------------------------------------------- validation ------------
def test_thirteen_widgets_is_422(client, viewer_headers):
    widgets = [_note_widget(f"n{i}") for i in range(13)]
    r = client.post(API, json={"name": "too many", "layout": {"version": 1, "widgets": widgets}},
                    headers=viewer_headers)
    assert r.status_code == 422


def test_viewer_saving_sql_widget_is_422(client, viewer_headers, source_conn_ds):
    conn, _ = source_conn_ds
    sqlw = {"id": _wid(), "title": "q", "span": 1, "type": "sql",
            "config": {"connection_id": conn["id"], "sql": "SELECT 1 AS n",
                       "viz": {"type": "number", "x": None, "y": "n"}}}
    r = client.post(API, json={"name": "viewer-sql", "layout": {"version": 1, "widgets": [sqlw]}},
                    headers=viewer_headers)
    assert r.status_code == 422
    assert "editor" in r.text.lower()


def test_non_select_sql_is_422_with_guard_message(client, editor_headers, source_conn_ds):
    conn, _ = source_conn_ds
    sqlw = {"id": _wid(), "title": "evil", "span": 1, "type": "sql",
            "config": {"connection_id": conn["id"], "sql": "DELETE FROM people",
                       "viz": {"type": "table", "x": None, "y": None}}}
    r = client.post(API, json={"name": "bad-sql", "layout": {"version": 1, "widgets": [sqlw]}},
                    headers=editor_headers)
    assert r.status_code == 422
    assert "select" in r.text.lower() or "allowed" in r.text.lower()


def test_sql_widget_unknown_connection_is_422(client, editor_headers):
    sqlw = {"id": _wid(), "title": "q", "span": 1, "type": "sql",
            "config": {"connection_id": 999999, "sql": "SELECT 1 AS n",
                       "viz": {"type": "number", "x": None, "y": "n"}}}
    r = client.post(API, json={"name": "no-conn", "layout": {"version": 1, "widgets": [sqlw]}},
                    headers=editor_headers)
    assert r.status_code == 422


def test_unknown_params_key_is_422(client, viewer_headers):
    w = _metric_widget(params={"bogus_key": "x"})
    r = client.post(API, json={"name": "bad-params", "layout": {"version": 1, "widgets": [w]}},
                    headers=viewer_headers)
    assert r.status_code == 422
    assert "bogus_key" in r.text


def test_note_markdown_over_limit_is_422(client, viewer_headers):
    w = {"id": _wid(), "title": "huge", "span": 1, "type": "note",
         "config": {"markdown": "x" * 5001}}
    r = client.post(API, json={"name": "huge-note", "layout": {"version": 1, "widgets": [w]}},
                    headers=viewer_headers)
    assert r.status_code == 422


def test_client_sent_snapshot_stripped_on_write(client, editor_headers, source_conn_ds):
    conn, _ = source_conn_ds
    sqlw = {"id": _wid(), "title": "q", "span": 1, "type": "sql",
            "config": {"connection_id": conn["id"], "sql": "SELECT 1 AS n",
                       "viz": {"type": "number", "x": None, "y": "n"}},
            # a malicious/forged snapshot the server must ignore
            "snapshot": {"columns": ["n"], "rows": [[999999]],
                         "refreshed_at": "2020-01-01T00:00:00", "error": None, "elapsed_ms": 0}}
    r = client.post(API, json={"name": "forged", "layout": {"version": 1, "widgets": [sqlw]}},
                    headers=editor_headers)
    assert r.status_code == 201, r.text
    got = r.json()["layout"]["widgets"][0]
    assert got.get("snapshot") in (None, {})  # forged snapshot stripped on write


# ----------------------------------------------------- refresh ---------------
def test_refresh_populates_snapshot(client, editor_headers, source_conn_ds):
    conn, _ = source_conn_ds
    sqlw = {"id": _wid(), "title": "rows", "span": 2, "type": "sql",
            "config": {"connection_id": conn["id"], "sql": "SELECT COUNT(*) AS n FROM people",
                       "viz": {"type": "number", "x": None, "y": "n"}}}
    did = client.post(API, json={"name": "refresh-me", "layout": {"version": 1, "widgets": [sqlw]}},
                      headers=editor_headers).json()["id"]
    r = client.post(f"{API}/{did}/refresh", headers=editor_headers)
    assert r.status_code == 200, r.text
    snap = r.json()["layout"]["widgets"][0]["snapshot"]
    assert snap["rows"][0][0] == 200
    assert snap["columns"] == ["n"]
    assert snap["refreshed_at"]
    assert snap["error"] is None


def test_refresh_isolates_broken_sql(client, editor_headers, source_conn_ds):
    conn, _ = source_conn_ds
    good = {"id": _wid(), "title": "good", "span": 1, "type": "sql",
            "config": {"connection_id": conn["id"], "sql": "SELECT COUNT(*) AS n FROM people",
                       "viz": {"type": "number", "x": None, "y": "n"}}}
    bad = {"id": _wid(), "title": "bad", "span": 1, "type": "sql",
           "config": {"connection_id": conn["id"], "sql": "SELECT * FROM no_such_table",
                      "viz": {"type": "table", "x": None, "y": None}}}
    did = client.post(API, json={"name": "mixed", "layout": {"version": 1, "widgets": [good, bad]}},
                      headers=editor_headers).json()["id"]
    out = client.post(f"{API}/{did}/refresh", headers=editor_headers).json()
    widgets = {w["title"]: w for w in out["layout"]["widgets"]}
    assert widgets["good"]["snapshot"]["error"] is None
    assert widgets["good"]["snapshot"]["rows"][0][0] == 200  # other widget still refreshed
    assert widgets["bad"]["snapshot"]["error"]  # broken one captured its error
    assert widgets["bad"]["snapshot"]["rows"] == []


def test_refresh_row_cap_enforced(client, editor_headers, source_conn_ds):
    conn, _ = source_conn_ds
    # 200 source rows; cap is 200 — request all rows, expect exactly the cap.
    sqlw = {"id": _wid(), "title": "all", "span": 2, "type": "sql",
            "config": {"connection_id": conn["id"], "sql": "SELECT id FROM people",
                       "viz": {"type": "table", "x": None, "y": None}}}
    did = client.post(API, json={"name": "capped", "layout": {"version": 1, "widgets": [sqlw]}},
                      headers=editor_headers).json()["id"]
    out = client.post(f"{API}/{did}/refresh", headers=editor_headers).json()
    rows = out["layout"]["widgets"][0]["snapshot"]["rows"]
    assert len(rows) <= 200


def test_viewer_cannot_refresh(client, viewer_headers, editor_headers, source_conn_ds):
    conn, _ = source_conn_ds
    did = client.post(
        API, json={"name": "team-refreshable", "visibility": "team",
                   "layout": {"version": 1, "widgets": [_note_widget()]}},
        headers=editor_headers,
    ).json()["id"]
    # viewer can see the team board but refresh is editor-only
    assert client.post(f"{API}/{did}/refresh", headers=viewer_headers).status_code == 403


# ----------------------------------------------------- visibility ------------
def test_team_listed_for_others_private_not(client, editor_headers, viewer_headers):
    team_id = client.post(
        API, json={"name": "team-board", "visibility": "team",
                   "layout": {"version": 1, "widgets": [_note_widget()]}},
        headers=editor_headers,
    ).json()["id"]
    priv_id = client.post(
        API, json={"name": "priv-board", "layout": {"version": 1, "widgets": [_note_widget()]}},
        headers=editor_headers,
    ).json()["id"]
    metas = client.get(API, headers=viewer_headers).json()
    ids = {m["id"] for m in metas}
    assert team_id in ids  # team dashboard listed for another user
    assert priv_id not in ids  # private one is not
