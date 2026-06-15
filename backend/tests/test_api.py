"""End-to-end API flow against the synthetic source DB:
connection -> tables -> register -> profile -> generate -> activate -> run ->
exceptions -> triage -> knowledge -> dashboard -> RCA fallback -> RBAC.
"""

from uuid import uuid4

from app import models
from app.db import session_factory
from tests.conftest import NULL_EMAILS, SOURCE_ROWS


def test_health_is_public(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["llm_enabled"] is False
    assert body["llm_provider"] is None  # no keys configured in tests
    assert resp.headers.get("X-Request-ID")  # correlation id on every response


def test_metrics_endpoint(client, admin_headers):
    client.get("/api/v1/datasets", headers=admin_headers)  # generate one request metric
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "dq_http_requests_total" in body
    assert "dq_source_query_seconds" in body
    assert "dq_llm_requests_total" in body or "dq_llm" in body  # registered even if zero


def test_auth_required(client):
    assert client.get("/api/v1/datasets").status_code == 401
    assert client.get("/api/v1/dashboard").status_code == 401


def test_full_flow(client, admin_headers, source_db):
    h = admin_headers

    # connection
    resp = client.post(
        "/api/v1/connections", json={"name": "test-shop", "dsn": source_db}, headers=h
    )
    assert resp.status_code == 201, resp.text
    conn = resp.json()
    assert conn["kind"] == "sqlite"
    assert "****" not in conn["dsn_masked"]  # sqlite DSN has no credentials to mask

    resp = client.post(f"/api/v1/connections/{conn['id']}/test", headers=h)
    assert resp.json()["ok"] is True

    # bad scheme rejected (mysql is a supported kind since issue #29, so use mongodb)
    resp = client.post(
        "/api/v1/connections", json={"name": "bad", "dsn": "mongodb://root@x/db"}, headers=h
    )
    assert resp.status_code == 400

    # tables
    resp = client.get(f"/api/v1/connections/{conn['id']}/tables", headers=h)
    tables = resp.json()
    assert any(t["table_name"] == "people" for t in tables)

    # register
    resp = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    )
    assert resp.status_code == 201
    ds = resp.json()[0]

    # preview
    resp = client.get(f"/api/v1/datasets/{ds['id']}/preview?limit=5", headers=h)
    assert len(resp.json()["rows"]) == 5

    # profile
    resp = client.post(f"/api/v1/datasets/{ds['id']}/profile", headers=h)
    assert resp.status_code == 200, resp.text
    profile = resp.json()
    assert profile["row_count"] == SOURCE_ROWS

    # knowledge
    resp = client.put(
        f"/api/v1/datasets/{ds['id']}/knowledge",
        json={
            "business_context": "Synthetic people table",
            "known_issues": "emails are sometimes missing",
            "importance": "high",
            "freshness_sla_hours": 48,
            "pii_columns": ["email"],
        },
        headers=h,
    )
    assert resp.status_code == 200
    assert client.get(f"/api/v1/datasets/{ds['id']}/knowledge", headers=h).json()[
        "importance"
    ] == "high"

    # generate (no LLM key -> heuristic fallback even with use_llm=True)
    resp = client.post(
        "/api/v1/checks/generate", json={"dataset_id": ds["id"], "use_llm": True}, headers=h
    )
    assert resp.status_code == 200, resp.text
    gen = resp.json()
    assert gen["mode"] == "heuristic"
    assert gen["created"] > 4
    assert all(c["status"] == "proposed" for c in gen["checks"])

    # regenerate skips duplicates
    resp = client.post(
        "/api/v1/checks/generate", json={"dataset_id": ds["id"], "use_llm": False}, headers=h
    )
    assert resp.json()["created"] == 0
    assert resp.json()["skipped_duplicates"] > 0

    # activate a generated check (id is fully populated -> not_null passes) and run it
    not_null_id = next(
        c for c in gen["checks"] if c["check_type"] == "not_null" and c["column_name"] == "id"
    )
    resp = client.patch(
        f"/api/v1/checks/{not_null_id['id']}", json={"status": "active"}, headers=h
    )
    assert resp.status_code == 200
    assert resp.json()["next_run_at"] is not None

    resp = client.post(f"/api/v1/checks/{not_null_id['id']}/run", headers=h)
    assert resp.status_code == 200, resp.text
    run = resp.json()
    assert run["status"] == "pass"
    assert run["violation_count"] == 0

    # a strict manual check definitely fails and captures exceptions
    resp = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds["id"],
            "check_type": "not_null",
            "column_name": "email",
            "severity": "error",
            "name": "strict email not null",
        },
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    strict_id = resp.json()["id"]
    run = client.post(f"/api/v1/checks/{strict_id}/run", headers=h).json()
    assert run["status"] == "fail"
    assert run["violation_count"] == NULL_EMAILS
    assert run["exception_count"] == NULL_EMAILS

    # write SQL in a custom check is rejected with 422 (not a 500)
    resp = client.post(
        "/api/v1/checks",
        json={"dataset_id": ds["id"], "check_type": "custom_sql", "params": {"sql": "DELETE FROM people"}},
        headers=h,
    )
    assert resp.status_code == 422

    # exceptions + triage (API v2 paged envelope, #57)
    resp = client.get(f"/api/v1/exceptions?run_id={run['id']}", headers=h)
    page = resp.json()
    assert page["total"] == NULL_EMAILS
    excs = page["items"]
    assert len(excs) == NULL_EMAILS
    assert excs[0]["status"] == "open"
    ids = [e["id"] for e in excs[:2]]
    resp = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": ids, "status": "expected", "note": "legacy rows, backfill pending"},
        headers=h,
    )
    assert resp.status_code == 200
    assert all(e["status"] == "expected" for e in resp.json())

    # dashboard
    dash = client.get("/api/v1/dashboard", headers=h).json()
    assert dash["datasets"] >= 1
    assert dash["open_exceptions"] >= 1
    assert dash["llm_enabled"] is False
    assert len(dash["trend"]) == 14

    # RCA degrades without a key
    resp = client.post(
        "/api/v1/rca/start", json={"check_run_id": run["id"], "question": ""}, headers=h
    )
    assert resp.status_code == 503

    # check types metadata
    types = client.get("/api/v1/checks/types", headers=h).json()
    assert {t["key"] for t in types} >= {"not_null", "unique", "ml_outlier", "custom_sql"}


def test_delete_dataset_cleans_dependents(client, admin_headers, source_db):
    h = admin_headers
    suffix = uuid4().hex[:8]
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"delete-dataset-{suffix}", "dsn": source_db},
        headers=h,
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]
    ds_id = ds["id"]

    assert client.post(f"/api/v1/datasets/{ds_id}/profile", headers=h).status_code == 200
    dash = client.post(
        "/api/v1/adhoc-dashboards/generate",
        json={"dataset_id": ds_id, "focus": "delete cleanup"},
        headers=h,
    ).json()
    saved_query = client.post(
        "/api/v1/queries",
        json={
            "connection_id": conn["id"],
            "dataset_id": ds_id,
            "name": "Pinned cleanup query",
            "sql": "SELECT id, email FROM people",
            "tags": ["cleanup"],
        },
        headers=h,
    ).json()
    notification = client.post(
        "/api/v1/notifications/rules",
        json={"dataset_id": ds_id, "channel": "slack", "target": "https://hook.example/delete"},
        headers=h,
    ).json()

    check = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds_id,
            "check_type": "not_null",
            "column_name": "email",
            "severity": "error",
            "name": "cleanup exception producer",
        },
        headers=h,
    ).json()
    run = client.post(f"/api/v1/checks/{check['id']}/run", headers=h).json()
    excs = client.get(f"/api/v1/exceptions?run_id={run['id']}", headers=h).json()["items"]
    exc_ids = [e["id"] for e in excs]
    assert exc_ids
    triaged = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": [exc_ids[0]], "status": "acknowledged", "note": "cleanup coverage"},
        headers=h,
    )
    assert triaged.status_code == 200, triaged.text

    with session_factory()() as db:
        rca = models.RcaSession(
            dataset_id=ds_id,
            check_run_id=run["id"],
            question="cleanup coverage",
            status="complete",
        )
        db.add(rca)
        db.commit()
        rca_id = rca.id

    resp = client.delete(f"/api/v1/datasets/{ds_id}", headers=h)
    assert resp.status_code == 204, resp.text

    with session_factory()() as db:
        assert db.get(models.Dataset, ds_id) is None
        assert db.query(models.Check).filter(models.Check.dataset_id == ds_id).count() == 0
        assert db.query(models.CheckRun).filter(models.CheckRun.dataset_id == ds_id).count() == 0
        assert db.query(models.ExceptionRecord).filter(models.ExceptionRecord.dataset_id == ds_id).count() == 0
        assert (
            db.query(models.ExceptionEvent)
            .filter(models.ExceptionEvent.exception_id.in_(exc_ids))
            .count()
            == 0
        )
        assert db.get(models.RcaSession, rca_id) is None
        assert db.get(models.AdhocDashboard, dash["id"]) is None
        assert db.get(models.NotificationRule, notification["id"]) is None
        assert db.get(models.SavedQuery, saved_query["id"]).dataset_id is None


def test_rbac_viewer_cannot_mutate(client, admin_headers):
    resp = client.post(
        "/api/v1/auth/users",
        json={"email": "viewer@example.com", "password": "viewer123", "role": "viewer"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    token = client.post(
        "/api/v1/auth/login", json={"email": "viewer@example.com", "password": "viewer123"}
    ).json()["access_token"]
    vh = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/v1/datasets", headers=vh).status_code == 200
    resp = client.post(
        "/api/v1/checks",
        json={"dataset_id": 1, "check_type": "not_null", "column_name": "email"},
        headers=vh,
    )
    assert resp.status_code == 403
    assert client.get("/api/v1/auth/users", headers=vh).status_code == 403  # admin only


def test_bad_login(client):
    resp = client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )
    assert resp.status_code == 401
