"""Check versioning + rollback (#185): v1 on create, a version per definition
edit, restore appends a new version, lifecycle-only changes don't version."""

import uuid

import pytest

from app.core import check_authoring
from app.db import init_db, session_factory
from app.models import Connection, Dataset


def _api_dataset(client, headers, source_db) -> dict:
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"cv-{uuid.uuid4().hex}", "dsn": source_db},
        headers=headers,
    ).json()
    return client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=headers,
    ).json()[0]


def _create_check(client, headers, ds_id, **over) -> dict:
    body = {
        "dataset_id": ds_id,
        "check_type": "range",
        "column_name": "age",
        "params": {"min": 0, "max": 120},
        "severity": "warn",
        "status": "active",
    }
    body.update(over)
    r = client.post("/api/v1/checks", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _versions(client, headers, check_id) -> list[dict]:
    r = client.get(f"/api/v1/checks/{check_id}/versions", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _editor_with_grant(client, admin_headers, email, conn_id, grant_role) -> dict:
    uid = client.post(
        "/api/v1/auth/users",
        json={"email": email, "password": "longenough1", "role": "editor"},
        headers=admin_headers,
    ).json()["id"]
    g = client.post(
        f"/api/v1/auth/users/{uid}/grants",
        json={"connection_id": conn_id, "role": grant_role},
        headers=admin_headers,
    )
    assert g.status_code == 201, g.text
    tok = client.post(
        "/api/v1/auth/login", json={"email": email, "password": "longenough1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_create_snapshots_v1(client, admin_headers, source_db):
    ds = _api_dataset(client, admin_headers, source_db)
    c = _create_check(client, admin_headers, ds["id"])
    vs = _versions(client, admin_headers, c["id"])
    assert len(vs) == 1
    assert vs[0]["version"] == 1
    assert vs[0]["is_current"] is True
    assert vs[0]["change_note"] == "created"
    assert vs[0]["params"] == {"min": 0, "max": 120}


def test_edit_versions_and_restore(client, admin_headers, source_db):
    ds = _api_dataset(client, admin_headers, source_db)
    cid = _create_check(client, admin_headers, ds["id"])["id"]

    # Edit the threshold -> v2
    r = client.patch(f"/api/v1/checks/{cid}", json={"params": {"min": 0, "max": 65}}, headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["params"] == {"min": 0, "max": 65}

    vs = _versions(client, admin_headers, cid)
    assert [v["version"] for v in vs] == [2, 1]  # newest first
    assert vs[0]["is_current"] is True and vs[1]["is_current"] is False

    # Restore v1 -> appends v3 carrying v1's params
    r = client.post(f"/api/v1/checks/{cid}/restore", json={"version": 1}, headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["params"] == {"min": 0, "max": 120}

    vs = _versions(client, admin_headers, cid)
    assert [v["version"] for v in vs] == [3, 2, 1]
    assert vs[0]["is_current"] is True
    assert vs[0]["change_note"] == "restored from v1"
    assert vs[0]["params"] == {"min": 0, "max": 120}


def test_lifecycle_change_does_not_version(client, admin_headers, source_db):
    ds = _api_dataset(client, admin_headers, source_db)
    cid = _create_check(client, admin_headers, ds["id"])["id"]
    # Pause: a status-only change must NOT create a new version.
    assert client.patch(f"/api/v1/checks/{cid}", json={"status": "disabled"}, headers=admin_headers).status_code == 200
    assert len(_versions(client, admin_headers, cid)) == 1


def test_restore_unknown_version_404(client, admin_headers, source_db):
    ds = _api_dataset(client, admin_headers, source_db)
    cid = _create_check(client, admin_headers, ds["id"])["id"]
    assert client.post(f"/api/v1/checks/{cid}/restore", json={"version": 99}, headers=admin_headers).status_code == 404


def test_restore_requires_editor(client, admin_headers, source_db):
    ds = _api_dataset(client, admin_headers, source_db)
    cid = _create_check(client, admin_headers, ds["id"])["id"]
    assert client.post(f"/api/v1/checks/{cid}/restore", json={"version": 1}).status_code == 401


def test_versions_and_restore_respect_connection_grants(client, admin_headers, source_db):
    """The version-history (read) and restore (write) endpoints scope through the
    check's dataset connection (#159 / PR #187 review): a user who can't see the
    connection gets 404, and a viewer-grant editor can read history but not restore."""
    conn = client.post(
        "/api/v1/connections", json={"name": f"cg-{uuid.uuid4().hex}", "dsn": source_db}, headers=admin_headers
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "products"}]},
        headers=admin_headers,
    ).json()[0]
    cid = _create_check(client, admin_headers, ds["id"])["id"]

    # Viewer grant on this connection: may READ history, may NOT restore (403).
    viewer = _editor_with_grant(
        client, admin_headers, f"cg-view-{uuid.uuid4().hex}@example.com", conn["id"], "viewer"
    )
    assert client.get(f"/api/v1/checks/{cid}/versions", headers=viewer).status_code == 200
    assert client.post(f"/api/v1/checks/{cid}/restore", json={"version": 1}, headers=viewer).status_code == 403

    # Editor, but granted only on a DIFFERENT connection -> no access here: 404 on both.
    other = client.post(
        "/api/v1/connections", json={"name": f"cg-other-{uuid.uuid4().hex}", "dsn": source_db}, headers=admin_headers
    ).json()
    outsider = _editor_with_grant(
        client, admin_headers, f"cg-none-{uuid.uuid4().hex}@example.com", other["id"], "editor"
    )
    assert client.get(f"/api/v1/checks/{cid}/versions", headers=outsider).status_code == 404
    assert client.post(f"/api/v1/checks/{cid}/restore", json={"version": 1}, headers=outsider).status_code == 404


def test_core_create_rejects_bad_enums(source_db):
    """The shared write path validates severity/status/schedule_kind so the
    assistant (which bypasses the Pydantic REST layer) can't write a check the
    scheduler would silently never run (#186 review)."""
    init_db()
    with session_factory()() as db:
        conn = Connection(name=f"cve-{uuid.uuid4().hex}", kind="sqlite", dsn=source_db)
        db.add(conn)
        db.flush()
        ds = Dataset(connection_id=conn.id, table_name="people", display_name="people")
        db.add(ds)
        db.flush()

        common = {"check_type": "not_null", "column_name": "email", "params": {}}
        with pytest.raises(ValueError, match="status"):
            check_authoring.create_check(db, None, ds, severity="warn", status="paused", **common)
        with pytest.raises(ValueError, match="severity"):
            check_authoring.create_check(db, None, ds, severity="critical", status="active", **common)
        with pytest.raises(ValueError, match="schedule_kind"):
            check_authoring.create_check(
                db, None, ds, severity="warn", status="active", schedule_kind="daily", **common
            )
