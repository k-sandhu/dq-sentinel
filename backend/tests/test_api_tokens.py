import hashlib
from datetime import timedelta

from app import models
from app.db import session_factory


def test_token_scope_and_lifecycle(client, admin_headers, source_db, unique_name, caplog):
    with session_factory()() as db:
        a = models.Connection(name=unique_name("token-a"), kind="sqlite", dsn=source_db)
        b = models.Connection(name=unique_name("token-b"), kind="sqlite", dsn=source_db)
        db.add_all([a, b])
        db.flush()
        da = models.Dataset(connection_id=a.id, table_name="people", display_name="a")
        other = models.Dataset(connection_id=b.id, table_name="people", display_name="b")
        db.add_all([da, other])
        db.commit()
        aid, daid, otherid = a.id, da.id, other.id
    body = {"name": unique_name("machine"), "role": "viewer", "connection_scope": [aid]}
    for bad in (None, [], ["1"], [True], [99999999]):
        assert (
            client.post(
                "/api/v1/tokens", headers=admin_headers, json={**body, "connection_scope": bad}
            ).status_code
            == 422
        )
    response = client.post("/api/v1/tokens", headers=admin_headers, json=body)
    assert response.status_code == 201, response.text
    created = response.json()
    secret = created["token"]
    tid = created["id"]
    headers = {"Authorization": f"Bearer {secret}"}
    for invalid in ("dqs_" + "0" * 32, "dqs_bad"):
        assert (
            client.get("/api/v1/datasets", headers={"Authorization": f"Bearer {invalid}"}).status_code == 401
        )
    digest = hashlib.sha256(secret.encode()).hexdigest()
    with session_factory()() as db:
        token = db.get(models.ApiToken, tid)
        assert token.token_hash == digest and secret not in str(token.__dict__)
    listing = client.get("/api/v1/tokens", headers=admin_headers).text
    assert secret not in listing and digest not in listing
    assert client.get(f"/api/v1/datasets/{daid}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/datasets/{otherid}", headers=headers).status_code == 404
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 403
    assert client.post("/api/v1/tokens", headers=headers, json=body).status_code == 403
    with session_factory()() as db:
        stamp = db.get(models.ApiToken, tid).last_used_at
        assert stamp is not None
    client.get("/api/v1/datasets", headers=headers)
    with session_factory()() as db:
        token = db.get(models.ApiToken, tid)
        assert token.last_used_at == stamp
        token.connection_scope = None
        db.commit()
    assert client.get("/api/v1/datasets", headers=headers).json() == []
    with session_factory()() as db:
        token = db.get(models.ApiToken, tid)
        token.connection_scope = [aid]
        token.expires_at = models.utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/datasets", headers=headers).status_code == 401
    with session_factory()() as db:
        token = db.get(models.ApiToken, tid)
        token.expires_at = models.utcnow() + timedelta(days=1)
        db.commit()
    assert client.delete(f"/api/v1/tokens/{tid}", headers=admin_headers).status_code == 204
    assert client.get("/api/v1/datasets", headers=headers).status_code == 401
    assert secret not in caplog.text


def test_editor_mutation_is_scoped_and_attributed(client, admin_headers, source_db, unique_name):
    with session_factory()() as db:
        a = models.Connection(name=unique_name("editor-a"), kind="sqlite", dsn=source_db)
        b = models.Connection(name=unique_name("editor-b"), kind="sqlite", dsn=source_db)
        db.add_all([a, b])
        db.flush()
        da = models.Dataset(connection_id=a.id, table_name="people", display_name="a")
        other = models.Dataset(connection_id=b.id, table_name="people", display_name="b")
        db.add_all([da, other])
        db.commit()
        aid, daid, otherid = a.id, da.id, other.id
    name = unique_name("editor")
    created = client.post(
        "/api/v1/tokens",
        headers=admin_headers,
        json={"name": name, "role": "editor", "connection_scope": [aid]},
    ).json()
    headers = {"Authorization": f"Bearer {created['token']}"}
    check = {"name": "machine check", "check_type": "not_null", "column_name": "email"}
    assert (
        client.post("/api/v1/checks", headers=headers, json={**check, "dataset_id": otherid}).status_code
        == 404
    )
    response = client.post("/api/v1/checks", headers=headers, json={**check, "dataset_id": daid})
    assert response.status_code == 201, response.text
    with session_factory()() as db:
        entry = (
            db.query(models.AuditEntry)
            .filter_by(action="check.create", entity_id=response.json()["id"])
            .one()
        )
        assert entry.user_id is None
        assert entry.detail["actor"] == f"token:{name}"


def test_machine_route_boundaries(client, admin_headers, source_db, unique_name):
    with session_factory()() as db:
        a = models.Connection(name=unique_name("boundary-a"), kind="sqlite", dsn=source_db)
        b = models.Connection(name=unique_name("boundary-b"), kind="sqlite", dsn=source_db)
        db.add_all([a, b])
        db.flush()
        da = models.Dataset(connection_id=a.id, table_name="people", display_name="a")
        other = models.Dataset(connection_id=b.id, table_name="people", display_name="b")
        db.add_all([da, other])
        db.commit()
        aid, daid, otherid = a.id, da.id, other.id
    check = {"name": "boundary", "check_type": "not_null", "column_name": "email", "dataset_id": otherid}
    created_check = client.post("/api/v1/checks", headers=admin_headers, json=check)
    assert created_check.status_code == 201, created_check.text
    check_id = created_check.json()["id"]
    run = client.post(f"/api/v1/checks/{check_id}/run", headers=admin_headers)
    assert run.status_code == 200, run.text
    exceptions = client.get(f"/api/v1/runs/{run.json()['id']}/exceptions", headers=admin_headers).json()
    assert exceptions

    def credential(role):
        response = client.post(
            "/api/v1/tokens",
            headers=admin_headers,
            json={"name": unique_name(role), "role": role, "connection_scope": [aid]},
        )
        return {"Authorization": f"Bearer {response.json()['token']}"}

    viewer = credential("viewer")
    editor = credential("editor")
    assert (
        client.post("/api/v1/checks", headers=viewer, json={**check, "dataset_id": daid}).status_code == 403
    )
    assert client.post(f"/api/v1/checks/{check_id}/run", headers=editor).status_code == 404
    assert (
        client.post(
            "/api/v1/exceptions/triage",
            headers=editor,
            json={"ids": [exceptions[0]["id"]], "status": "resolved"},
        ).status_code
        == 404
    )
    assert client.get("/api/v1/exceptions?assignee=me", headers=editor).status_code == 403
    assert client.get(f"/api/v1/datasets/{daid}/profile", headers=editor).status_code == 403
    assert (
        client.patch(f"/api/v1/checks/{check_id}", headers=editor, json={"name": "changed"}).status_code
        == 403
    )


def test_deleting_connection_removes_token_scope(client, admin_headers, source_db, unique_name):
    with session_factory()() as db:
        connection = models.Connection(name=unique_name("removed"), kind="sqlite", dsn=source_db)
        db.add(connection)
        db.commit()
        cid = connection.id
    created = client.post(
        "/api/v1/tokens",
        headers=admin_headers,
        json={"name": unique_name("remove-scope"), "connection_scope": [cid]},
    ).json()
    assert client.delete(f"/api/v1/connections/{cid}", headers=admin_headers).status_code == 204
    with session_factory()() as db:
        assert db.get(models.ApiToken, created["id"]).connection_scope == []
    assert (
        client.get("/api/v1/datasets", headers={"Authorization": f"Bearer {created['token']}"}).json() == []
    )
