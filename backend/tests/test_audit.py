"""Audit log (issue #30): in-band writes at instrumented call sites, the
same-transaction property, the admin-only /audit viewer with filters + paging,
and the no-secrets guarantee.
"""

from datetime import timedelta

from app.core.scheduler import purge_audit_log
from app.db import session_factory
from app.models import AuditEntry, utcnow


def _audit(client, headers, **params):
    resp = client.get("/api/v1/audit", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_login_success_and_failure_write_rows(client, admin_headers):
    # success (admin_headers fixture already logged in once; do it again explicitly)
    client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    ok = _audit(client, admin_headers, action="login.success")
    assert ok["total"] >= 1
    assert all(r["action"] == "login.success" for r in ok["items"])
    assert ok["items"][0]["user"]  # resolved display name

    # failure: user_id is None, email captured, but no password material
    client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong"})
    fail = _audit(client, admin_headers, action="login.failure")
    assert fail["total"] >= 1
    top = fail["items"][0]
    assert top["user_id"] is None
    assert top["user"] is None
    assert top["detail"].get("email") == "admin@example.com"
    assert "wrong" not in str(top["detail"])
    assert "password" not in top["detail"]


def test_check_update_writes_params_diff(client, admin_headers, source_db):
    h = admin_headers
    conn = client.post(
        "/api/v1/connections", json={"name": "audit-src", "dsn": source_db}, headers=h
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]
    check = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds["id"],
            "check_type": "not_null",
            "column_name": "email",
            "params": {"tolerance": 0},
            "name": "audit check",
        },
        headers=h,
    ).json()

    # creating the check wrote a check.create row
    created = _audit(client, h, action="check.create", entity_type="check")
    assert any(r["entity_id"] == check["id"] for r in created["items"])

    # update the tolerance -> params diff recorded
    client.patch(f"/api/v1/checks/{check['id']}", json={"params": {"tolerance": 5}}, headers=h)
    upd = _audit(client, h, action="check.update")
    row = next(r for r in upd["items"] if r["entity_id"] == check["id"])
    assert row["detail"]["params_before"] == {"tolerance": 0}
    assert row["detail"]["params_after"] == {"tolerance": 5}

    # manual run is audited
    client.post(f"/api/v1/checks/{check['id']}/run", headers=h)
    assert _audit(client, h, action="check.run_manual")["total"] >= 1

    # archive is audited
    client.delete(f"/api/v1/checks/{check['id']}", headers=h)
    assert _audit(client, h, action="check.archive")["total"] >= 1


def test_triage_writes_one_batch_row(client, admin_headers, source_db):
    h = admin_headers
    conn = client.post(
        "/api/v1/connections", json={"name": "audit-triage", "dsn": source_db}, headers=h
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]
    check = client.post(
        "/api/v1/checks",
        json={"dataset_id": ds["id"], "check_type": "not_null", "column_name": "email"},
        headers=h,
    ).json()
    run = client.post(f"/api/v1/checks/{check['id']}/run", headers=h).json()
    excs = client.get(f"/api/v1/exceptions?run_id={run['id']}", headers=h).json()
    assert len(excs) >= 2
    ids = [e["id"] for e in excs]

    before = _audit(client, h, action="exception.triage")["total"]
    resp = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": ids, "status": "expected", "note": "batch"},
        headers=h,
    )
    assert resp.status_code == 200
    after = _audit(client, h, action="exception.triage")
    assert after["total"] == before + 1  # ONE row for the whole batch
    row = next(r for r in after["items"] if r["detail"].get("count") == len(ids))
    assert row["detail"]["status"] == "expected"


def test_failed_endpoint_leaves_no_audit_row(client, admin_headers):
    """Same-transaction property: a 404 update must not persist an audit row."""
    h = admin_headers
    before = _audit(client, h, action="check.update")["total"]
    assert client.patch("/api/v1/checks/99999999", json={"name": "x"}, headers=h).status_code == 404
    after = _audit(client, h, action="check.update")["total"]
    assert after == before


def test_no_dsn_or_password_in_any_detail(client, admin_headers, source_db):
    """Scan connection + user audit rows for forbidden material."""
    h = admin_headers
    secret_dsn = source_db  # sqlite path; still must never appear verbatim
    client.post("/api/v1/connections", json={"name": "secret-conn", "dsn": secret_dsn}, headers=h)
    client.post(
        "/api/v1/auth/users",
        json={"email": "audited@example.com", "password": "supersecret123", "role": "viewer"},
        headers=h,
    )

    # connection.create row carries name/kind, never the DSN
    conn_rows = _audit(client, h, entity_type="connection")
    assert conn_rows["total"] >= 1
    for r in conn_rows["items"]:
        blob = str(r["detail"])
        assert secret_dsn not in blob
        assert "dsn" not in r["detail"]
        assert r["detail"].get("name")  # name IS recorded

    # user.create row carries email/role, never the password
    user_rows = _audit(client, h, action="user.create")
    for r in user_rows["items"]:
        blob = str(r["detail"])
        assert "supersecret123" not in blob
        assert "password" not in r["detail"]
        assert "password_hash" not in r["detail"]


def test_audit_admin_only_and_filters_and_paging(client, admin_headers):
    h = admin_headers
    # editor is forbidden
    client.post(
        "/api/v1/auth/users",
        json={"email": "audit-editor@example.com", "password": "editor123", "role": "editor"},
        headers=h,
    )
    token = client.post(
        "/api/v1/auth/login", json={"email": "audit-editor@example.com", "password": "editor123"}
    ).json()["access_token"]
    eh = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/audit", headers=eh).status_code == 403

    # envelope shape
    page = _audit(client, h, limit=5)
    assert set(page.keys()) == {"items", "total", "limit", "offset"}
    assert page["limit"] == 5
    assert len(page["items"]) <= 5

    # default sort id desc
    page2 = _audit(client, h, limit=10)
    ids = [r["id"] for r in page2["items"]]
    assert ids == sorted(ids, reverse=True)

    # q is an action prefix match
    logins = _audit(client, h, q="login")
    assert logins["total"] >= 1
    assert all(r["action"].startswith("login") for r in logins["items"])

    # paging: offset advances past the first page without overlap
    if page2["total"] > 5:
        first = _audit(client, h, limit=5, offset=0)
        second = _audit(client, h, limit=5, offset=5)
        first_ids = {r["id"] for r in first["items"]}
        second_ids = {r["id"] for r in second["items"]}
        assert first_ids.isdisjoint(second_ids)


def test_purge_respects_retention(client, admin_headers, monkeypatch):
    """Old rows are purged; recent rows survive. Reset the daily throttle first."""
    import app.core.scheduler as sched

    factory = session_factory()
    with factory() as db:
        old = AuditEntry(
            action="login.success",
            entity_type="user",
            created_at=utcnow() - timedelta(days=400),
        )
        recent = AuditEntry(
            action="login.success", entity_type="user", created_at=utcnow()
        )
        db.add_all([old, recent])
        db.commit()
        old_id, recent_id = old.id, recent.id

    monkeypatch.setattr(sched, "_last_audit_purge", None)
    deleted = purge_audit_log()
    assert deleted >= 1

    with factory() as db:
        assert db.get(AuditEntry, old_id) is None
        assert db.get(AuditEntry, recent_id) is not None

    # second immediate call is throttled (no-op within 24h)
    assert purge_audit_log() == 0
