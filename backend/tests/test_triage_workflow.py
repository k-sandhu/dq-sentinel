"""Triage workflow: assignment, comments, append-only events (#56)."""

import pytest


@pytest.fixture(scope="module")
def workflow(client, admin_headers, source_db):
    """Register a dataset + a failing check, capture exceptions, return ids/headers."""
    h = admin_headers
    conn = client.post(
        "/api/v1/connections", json={"name": "tw-src", "dsn": source_db}, headers=h
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
            "severity": "error",
            "name": "tw strict email",
        },
        headers=h,
    ).json()
    run = client.post(f"/api/v1/checks/{check['id']}/run", headers=h).json()
    excs = client.get(f"/api/v1/exceptions?run_id={run['id']}", headers=h).json()
    items = excs["items"] if isinstance(excs, dict) else excs
    assert len(items) >= 3

    # An editor user for mutation tests.
    client.post(
        "/api/v1/auth/users",
        json={"email": "tw-editor@example.com", "password": "editor123", "role": "editor"},
        headers=h,
    )
    etoken = client.post(
        "/api/v1/auth/login", json={"email": "tw-editor@example.com", "password": "editor123"}
    ).json()["access_token"]
    # A viewer user.
    client.post(
        "/api/v1/auth/users",
        json={"email": "tw-viewer@example.com", "password": "viewer123", "role": "viewer"},
        headers=h,
    )
    vtoken = client.post(
        "/api/v1/auth/login", json={"email": "tw-viewer@example.com", "password": "viewer123"}
    ).json()["access_token"]

    return {
        "ids": [e["id"] for e in items],
        "admin": h,
        "editor": {"Authorization": f"Bearer {etoken}"},
        "viewer": {"Authorization": f"Bearer {vtoken}"},
    }


def test_two_comments_produce_two_ordered_events(client, workflow):
    exc_id = workflow["ids"][0]
    h = workflow["editor"]
    r1 = client.post(f"/api/v1/exceptions/{exc_id}/comments", json={"comment": "first"}, headers=h)
    assert r1.status_code == 201, r1.text
    r2 = client.post(f"/api/v1/exceptions/{exc_id}/comments", json={"comment": "second"}, headers=h)
    assert r2.status_code == 201

    events = client.get(f"/api/v1/exceptions/{exc_id}/events", headers=h).json()
    comments = [e for e in events if e["kind"] == "comment"]
    assert [c["comment"] for c in comments] == ["first", "second"]  # ascending order preserved
    # `note` reflects the latest comment.
    exc = client.get("/api/v1/exceptions", headers=h).json()
    items = exc["items"] if isinstance(exc, dict) else exc
    this = next(e for e in items if e["id"] == exc_id)
    assert this["note"] == "second"


def test_status_change_writes_from_to_and_noop_is_422(client, workflow):
    exc_id = workflow["ids"][1]
    h = workflow["editor"]
    r = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": [exc_id], "status": "acknowledged", "note": "looking into it"},
        headers=h,
    )
    assert r.status_code == 200
    events = client.get(f"/api/v1/exceptions/{exc_id}/events", headers=h).json()
    status_ev = [e for e in events if e["kind"] == "status"][-1]
    assert status_ev["from_status"] == "open"
    assert status_ev["to_status"] == "acknowledged"

    # Empty body -> 422.
    r = client.post("/api/v1/exceptions/triage", json={"ids": [exc_id]}, headers=h)
    assert r.status_code == 422


def test_assign_and_clear_roundtrip(client, workflow):
    exc_id = workflow["ids"][2]
    h = workflow["editor"]
    # Resolve an assignee id via the non-admin assignees endpoint.
    assignees = client.get("/api/v1/auth/assignees", headers=h).json()
    target = next(a for a in assignees if a["email"] == "tw-editor@example.com")

    r = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": [exc_id], "assigned_to_id": target["id"]},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()[0]["assigned_to_id"] == target["id"]
    assert r.json()[0]["assigned_to"] is not None

    r = client.post(
        "/api/v1/exceptions/triage", json={"ids": [exc_id], "clear_assignee": True}, headers=h
    )
    assert r.status_code == 200
    assert r.json()[0]["assigned_to_id"] is None

    events = client.get(f"/api/v1/exceptions/{exc_id}/events", headers=h).json()
    assert sum(1 for e in events if e["kind"] == "assign") == 2  # assign + unassign


def test_assign_unknown_user_is_422(client, workflow):
    exc_id = workflow["ids"][0]
    h = workflow["editor"]
    r = client.post(
        "/api/v1/exceptions/triage", json={"ids": [exc_id], "assigned_to_id": 999999}, headers=h
    )
    assert r.status_code == 422


def test_assign_inactive_user_is_422(client, workflow):
    h = workflow["admin"]
    # Create then deactivate a user.
    u = client.post(
        "/api/v1/auth/users",
        json={"email": "tw-gone@example.com", "password": "gone1234", "role": "editor"},
        headers=h,
    ).json()
    client.patch(f"/api/v1/auth/users/{u['id']}", json={"is_active": False}, headers=h)
    r = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": [workflow["ids"][0]], "assigned_to_id": u["id"]},
        headers=workflow["editor"],
    )
    assert r.status_code == 422


def test_assignees_visible_to_viewer_users_admin_only(client, workflow):
    vh = workflow["viewer"]
    # Viewer can read the assignee list...
    assert client.get("/api/v1/auth/assignees", headers=vh).status_code == 200
    # ...but /auth/users is still admin-only.
    assert client.get("/api/v1/auth/users", headers=vh).status_code == 403
    # Deactivated users are excluded from the assignee list.
    emails = {a["email"] for a in client.get("/api/v1/auth/assignees", headers=vh).json()}
    assert "tw-gone@example.com" not in emails


def test_comment_requires_editor_role(client, workflow):
    exc_id = workflow["ids"][0]
    r = client.post(
        f"/api/v1/exceptions/{exc_id}/comments",
        json={"comment": "viewer attempt"},
        headers=workflow["viewer"],
    )
    assert r.status_code == 403
    # Viewers CAN read the timeline (transparency).
    assert client.get(f"/api/v1/exceptions/{exc_id}/events", headers=workflow["viewer"]).status_code == 200


def test_events_404_on_unknown_exception(client, workflow):
    assert client.get("/api/v1/exceptions/999999/events", headers=workflow["editor"]).status_code == 404


def test_bulk_cap_enforced(client, workflow):
    r = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": list(range(1, 1002)), "status": "muted"},
        headers=workflow["editor"],
    )
    assert r.status_code == 422
