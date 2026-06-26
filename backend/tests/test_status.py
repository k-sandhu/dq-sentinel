"""Read-only data Status page API (D9 / #179).

Proves the allowlist shape is viewer-safe: no PII / external_refs / dedupe_key /
event-detail blobs / user names leak; internal ops events are dropped; the view is
grant-scoped; health derives correctly; samples are capped; and auth is required.

The app DB is session-shared, so every entity uses a globally-unique name and the
assertions key off those names (never global counts).
"""

from app.db import session_factory
from app.models import Check, Connection, Dataset, Incident, IncidentEvent

_Session = session_factory()


def _login(client, email, password):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _scenario(db, *, conn_name, table, last_status, dedupe, secret, with_unsafe=False, n_safe=1):
    """A connection + monitored dataset (active check at `last_status`) + an open
    incident with a safe `resolved` event and, optionally, unsafe ops events whose
    detail/dedupe must never reach the wire. Returns (connection, dataset)."""
    conn = Connection(name=conn_name, kind="sqlite", dsn="sqlite://")
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, schema_name="public", table_name=table, display_name=table)
    db.add(ds)
    db.flush()
    chk = Check(dataset_id=ds.id, name=f"{table}-chk", check_type="not_null", status="active", last_status=last_status)
    db.add(chk)
    db.flush()
    inc = Incident(
        dataset_id=ds.id,
        check_id=chk.id,
        dedupe_key=dedupe,
        title=f"{table} not_null failing",
        severity="error",
        status="open",
        external_refs={"jira": secret},
    )
    db.add(inc)
    db.flush()
    for _ in range(n_safe):
        db.add(IncidentEvent(incident_id=inc.id, kind="resolved", detail={}))
    if with_unsafe:
        db.add(IncidentEvent(incident_id=inc.id, kind="notified", detail={"webhook": secret}))
        db.add(IncidentEvent(incident_id=inc.id, kind="system", detail={"note": secret}))
        db.add(IncidentEvent(incident_id=inc.id, kind="escalated", detail={"to": secret}))
    db.flush()
    return conn, ds


def test_status_redacts_and_drops_unsafe_events(client, admin_headers):
    secret = "D9-SECRET-must-not-leak-xyz"
    dedupe = "d9-secret-dedupe-key-abc"
    with _Session() as db:
        _scenario(db, conn_name="d9-redact-conn", table="d9_redact_orders",
                  last_status="fail", dedupe=dedupe, secret=secret, with_unsafe=True)
        db.commit()

    body = client.get("/api/v1/status", headers=admin_headers)
    assert body.status_code == 200, body.text
    raw = body.text
    # the planted secrets (external_refs value, dedupe_key, event-detail blobs) never ship
    assert secret not in raw
    assert dedupe not in raw

    data = body.json()
    tile = next(t for t in data["datasets"] if t["name"] == "d9_redact_orders")
    assert tile["health"] == "degraded"
    assert tile["open_incidents"] >= 1
    # the safe event surfaces; the unsafe ops kinds are dropped
    mine = [u for u in data["updates"] if u["dataset_name"] == "d9_redact_orders"]
    assert mine and all(u["kind"] == "resolved" for u in mine)
    assert {"notified", "system", "escalated"}.isdisjoint({u["kind"] for u in data["updates"]})
    # allowlist: an update carries only the public fields, never detail/user/refs
    assert set(mine[0]) == {"kind", "title", "dataset_name", "severity", "at"}


def test_status_is_grant_scoped(client, admin_headers):
    with _Session() as db:
        ca, _ = _scenario(db, conn_name="d9-scope-A", table="d9_scope_a",
                          last_status="fail", dedupe="d9-A", secret="A")
        _scenario(db, conn_name="d9-scope-B", table="d9_scope_b",
                  last_status="fail", dedupe="d9-B", secret="B")
        db.commit()
        ca_id = ca.id

    # a scoped editor granted only connection A
    u = client.post("/api/v1/auth/users",
                    json={"email": "d9-scoped@x.com", "name": "S", "password": "password1", "role": "editor"},
                    headers=admin_headers).json()
    g = client.post(f"/api/v1/auth/users/{u['id']}/grants",
                    json={"connection_id": ca_id, "role": "viewer"}, headers=admin_headers)
    assert g.status_code == 201, g.text

    def names(h):
        return {t["name"] for t in client.get("/api/v1/status", headers=h).json()["datasets"]}

    scoped = names(_login(client, "d9-scoped@x.com", "password1"))
    assert "d9_scope_a" in scoped and "d9_scope_b" not in scoped  # only the granted connection
    admin = names(admin_headers)
    assert "d9_scope_a" in admin and "d9_scope_b" in admin  # admin/zero-grant = unrestricted


def test_status_health_derivation(client, admin_headers):
    with _Session() as db:
        _scenario(db, conn_name="d9-health-conn", table="d9_health_ok", last_status="pass", dedupe="d9-h1", secret="x")
        _scenario(db, conn_name="d9-health-conn2", table="d9_health_warn", last_status="warn", dedupe="d9-h2", secret="x")
        db.commit()
    tiles = {t["name"]: t["health"] for t in client.get("/api/v1/status", headers=admin_headers).json()["datasets"]}
    assert tiles["d9_health_ok"] == "operational"
    assert tiles["d9_health_warn"] == "delayed"


def test_status_caps_updates(client, admin_headers):
    from app.api.status import UPDATES_CAP

    with _Session() as db:
        _scenario(db, conn_name="d9-cap-conn", table="d9_cap_ds",
                  last_status="fail", dedupe="d9-cap", secret="x", n_safe=UPDATES_CAP + 5)
        db.commit()
    updates = client.get("/api/v1/status", headers=admin_headers).json()["updates"]
    assert len(updates) <= UPDATES_CAP  # cap enforced server-side, not just in the UI


def test_status_requires_auth(client):
    assert client.get("/api/v1/status").status_code == 401
