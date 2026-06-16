"""SLA tracking (#102): attainment/breach evaluation, CRUD API, knowledge auto-create."""

import uuid

from app.core.runner import run_check
from app.core.sla import evaluate_all, evaluate_sla
from app.db import init_db, session_factory
from app.models import Check, Connection, Dataset, SLADefinition


def _dataset_with_check(db, source_db, *, column="id"):
    conn = Connection(name=f"sla-{uuid.uuid4().hex}", kind="sqlite", dsn=source_db)
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, table_name="people", display_name="people")
    db.add(ds)
    db.flush()
    check = Check(
        dataset_id=ds.id,
        name="nn",
        check_type="not_null",
        column_name=column,
        severity="error",
        status="active",
        params={},
    )
    db.add(check)
    db.commit()
    db.refresh(check)
    return ds, check


def test_attainment_and_breach(source_db):
    init_db()
    factory = session_factory()
    with factory() as db:
        ds, check = _dataset_with_check(db, source_db, column="id")  # not_null id -> passes
        for _ in range(4):
            run_check(db, check)  # 4 good runs
        sla = SLADefinition(
            name="check sla", scope="check", scope_id=check.id,
            target_type="check_success", objective=0.99, window="rolling_30d",
        )
        db.add(sla)
        db.flush()
        ev = evaluate_sla(db, sla)
        db.commit()
        assert ev.good == 4 and ev.bad == 0
        assert ev.attainment == 1.0 and ev.breached is False
        assert ev.budget_consumed == 0.0

        # Now make the check fail (email has NULLs) and run twice -> 4 good / 2 bad.
        check.column_name = "email"
        db.commit()
        for _ in range(2):
            run_check(db, check)
        ev2 = evaluate_sla(db, sla)
        db.commit()
        assert ev2.good == 4 and ev2.bad == 2
        assert round(ev2.attainment, 3) == 0.667
        assert ev2.breached is True  # 0.667 < 0.99


def test_evaluate_all_runs_for_enabled(source_db):
    init_db()
    factory = session_factory()
    with factory() as db:
        ds, check = _dataset_with_check(db, source_db, column="id")
        run_check(db, check)
        db.add(SLADefinition(name="ds sla", scope="dataset", scope_id=ds.id,
                             target_type="check_success", objective=0.9))
        db.commit()
        evals = evaluate_all(db)
        assert evals  # at least our SLA evaluated
        assert all(e.id is not None for e in evals)


def test_no_runs_is_vacuously_met(source_db):
    init_db()
    factory = session_factory()
    with factory() as db:
        ds, _check = _dataset_with_check(db, source_db, column="id")
        # SLA over freshness, but there is no freshness check -> no runs -> attainment 1.0
        sla = SLADefinition(name="fresh", scope="dataset", scope_id=ds.id,
                            target_type="freshness", objective=0.99)
        db.add(sla)
        db.flush()
        ev = evaluate_sla(db, sla)
        assert ev.good == 0 and ev.bad == 0
        assert ev.attainment == 1.0 and ev.breached is False


# ---- API ----
def _api_dataset(client, headers, source_db):
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"slaapi-{uuid.uuid4().hex}", "dsn": source_db},
        headers=headers,
    ).json()
    return client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=headers,
    ).json()[0]


def test_sla_api_crud(client, admin_headers, source_db):
    ds = _api_dataset(client, admin_headers, source_db)
    r = client.post(
        "/api/v1/sla",
        json={"name": "People reliability", "scope": "dataset", "scope_id": ds["id"],
              "target_type": "check_success", "objective": 0.95, "window": "rolling_7d"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    sla = r.json()
    assert sla["dataset_id"] == ds["id"]
    assert sla["scope_label"]
    assert sla["latest"] is not None  # seeded on create
    sid = sla["id"]

    listed = client.get(f"/api/v1/sla?dataset_id={ds['id']}", headers=admin_headers).json()
    assert any(s["id"] == sid for s in listed)

    rel = client.get("/api/v1/sla/reliability", headers=admin_headers).json()
    assert rel["total"] >= 1 and isinstance(rel["slas"], list)

    detail = client.get(f"/api/v1/sla/{sid}", headers=admin_headers).json()
    assert len(detail["evaluations"]) >= 1

    patched = client.patch(f"/api/v1/sla/{sid}", json={"objective": 0.5}, headers=admin_headers)
    assert patched.json()["objective"] == 0.5

    assert client.post(f"/api/v1/sla/{sid}/evaluate", headers=admin_headers).status_code == 200
    assert client.delete(f"/api/v1/sla/{sid}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/v1/sla/{sid}", headers=admin_headers).status_code == 404


def test_sla_create_requires_editor(client):
    assert client.post("/api/v1/sla", json={"scope": "dataset", "scope_id": 1}).status_code == 401


def test_freshness_sla_autocreated_from_knowledge(client, admin_headers, source_db):
    ds = _api_dataset(client, admin_headers, source_db)
    r = client.put(
        f"/api/v1/datasets/{ds['id']}/knowledge",
        json={"freshness_sla_hours": 24},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    slas = client.get(f"/api/v1/sla?dataset_id={ds['id']}", headers=admin_headers).json()
    assert any(s["target_type"] == "freshness" for s in slas)
