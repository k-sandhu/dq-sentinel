"""Per-connection grants foundation (#26 PR2 / #159): the visibility helpers and
the admin grants CRUD. The surface-scoping sweep that USES these helpers is #72
(separate PR); here we prove the model + semantics + management API in isolation.
"""

import pytest
from fastapi import HTTPException

from app.db import init_db, session_factory
from app.models import Connection, ConnectionGrant, User
from app.security import assert_connection_visible, connection_role, visible_connection_ids


def _user(db, email, role="editor"):
    u = User(email=email, name=email, password_hash="x", role=role)
    db.add(u)
    db.flush()
    return u


def _conn(db, name):
    c = Connection(name=name, kind="sqlite", dsn="sqlite://")
    db.add(c)
    db.flush()
    return c


def test_visibility_semantics():
    init_db()  # ensure migrations (incl. connection_grants) have run
    factory = session_factory()
    with factory() as db:
        admin = _user(db, "g-admin@x.com", role="admin")
        zero = _user(db, "g-zero@x.com", role="editor")  # no grants -> legacy
        scoped = _user(db, "g-scoped@x.com", role="editor")
        a = _conn(db, "g-conn-a")
        b = _conn(db, "g-conn-b")
        db.add(ConnectionGrant(user_id=scoped.id, connection_id=a.id, role="editor"))
        db.flush()

        # visible_connection_ids: None = unrestricted, set = restrict
        assert visible_connection_ids(db, admin) is None  # admin bypasses grants
        assert visible_connection_ids(db, zero) is None  # zero grants -> legacy full
        assert visible_connection_ids(db, scoped) == {a.id}  # restricted to granted

        # connection_role: effective role per connection (None = no access)
        assert connection_role(db, admin, b.id) == "admin"
        assert connection_role(db, zero, b.id) == "editor"  # legacy global role
        assert connection_role(db, scoped, a.id) == "editor"  # the grant's role
        assert connection_role(db, scoped, b.id) is None  # ungranted -> no access

        # A nonexistent connection has no role for anyone, and the by-id gate 404s
        # for missing OR invisible (don't leak existence) (#167 review).
        assert connection_role(db, admin, 999999) is None
        assert connection_role(db, zero, 999999) is None
        assert connection_role(db, scoped, 999999) is None
        with pytest.raises(HTTPException):
            assert_connection_visible(db, admin, 999999)  # missing -> 404
        with pytest.raises(HTTPException):
            assert_connection_visible(db, scoped, b.id)  # invisible -> 404
        assert assert_connection_visible(db, scoped, a.id).id == a.id  # visible -> returns it

        db.rollback()  # uncommitted: don't pollute the shared session DB


def test_deleting_granted_connection_removes_grants(client, admin_headers, source_db):
    """A granted connection must still be deletable — grants are cleaned up
    (explicit delete + FK ondelete=CASCADE) rather than blocking it (#167 review).
    """
    h = admin_headers
    u = client.post(
        "/api/v1/auth/users",
        json={"email": "del-grantee@x.com", "name": "D", "password": "password1", "role": "editor"},
        headers=h,
    ).json()
    conn = client.post(
        "/api/v1/connections", json={"name": "grant-del-src", "dsn": source_db}, headers=h
    ).json()
    g = client.post(
        f"/api/v1/auth/users/{u['id']}/grants",
        json={"connection_id": conn["id"], "role": "viewer"},
        headers=h,
    )
    assert g.status_code == 201
    d = client.delete(f"/api/v1/connections/{conn['id']}", headers=h)
    assert d.status_code == 204, d.text
    assert client.get(f"/api/v1/auth/users/{u['id']}/grants", headers=h).json() == []


def test_grants_crud_admin_only(client, admin_headers, source_db):
    h = admin_headers
    u = client.post(
        "/api/v1/auth/users",
        json={"email": "grantee@x.com", "name": "G", "password": "password1", "role": "editor"},
        headers=h,
    ).json()
    conn = client.post(
        "/api/v1/connections", json={"name": "grants-crud-src", "dsn": source_db}, headers=h
    ).json()

    # admin grants viewer access
    r = client.post(
        f"/api/v1/auth/users/{u['id']}/grants",
        json={"connection_id": conn["id"], "role": "viewer"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "viewer"
    assert r.json()["connection_name"] == "grants-crud-src"

    # upsert: re-granting updates the role rather than duplicating
    r2 = client.post(
        f"/api/v1/auth/users/{u['id']}/grants",
        json={"connection_id": conn["id"], "role": "editor"},
        headers=h,
    )
    assert r2.status_code == 201
    assert r2.json()["role"] == "editor"
    listed = client.get(f"/api/v1/auth/users/{u['id']}/grants", headers=h).json()
    assert len(listed) == 1 and listed[0]["role"] == "editor"

    # a non-admin cannot view or manage grants
    tok = client.post(
        "/api/v1/auth/login", json={"email": "grantee@x.com", "password": "password1"}
    ).json()["access_token"]
    nah = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"/api/v1/auth/users/{u['id']}/grants", headers=nah).status_code == 403
    assert (
        client.post(
            f"/api/v1/auth/users/{u['id']}/grants",
            json={"connection_id": conn["id"], "role": "viewer"},
            headers=nah,
        ).status_code
        == 403
    )

    # revoke
    assert client.delete(f"/api/v1/auth/users/{u['id']}/grants/{conn['id']}", headers=h).status_code == 204
    assert client.get(f"/api/v1/auth/users/{u['id']}/grants", headers=h).json() == []

    # grant on a missing connection -> 404
    assert (
        client.post(
            f"/api/v1/auth/users/{u['id']}/grants",
            json={"connection_id": 999999, "role": "viewer"},
            headers=h,
        ).status_code
        == 404
    )
