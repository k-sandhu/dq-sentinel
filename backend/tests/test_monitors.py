import uuid

from app import models
from app.core.check_types import CHECK_TYPES
from app.db import session_factory


def _register_dataset(client, headers, source_db, *, suffix: str | None = None) -> dict:
    suffix = suffix or uuid.uuid4().hex[:8]
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"monitor-pack-{suffix}", "dsn": source_db},
        headers=headers,
    )
    assert conn.status_code == 201, conn.text
    resp = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn.json()["id"], "tables": [{"table_name": "people"}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]


def test_pack_row_created_on_dataset_registration(client, admin_headers, source_db):
    ds = _register_dataset(client, admin_headers, source_db)

    with session_factory()() as db:
        pack = (
            db.query(models.DatasetMonitorPack)
            .filter(models.DatasetMonitorPack.dataset_id == ds["id"])
            .first()
        )
        assert pack is not None
        assert pack.enabled is True
        assert pack.status == "pending_profile"


def test_profile_reconciliation_creates_active_system_checks(client, admin_headers, source_db):
    ds = _register_dataset(client, admin_headers, source_db)
    prof = client.post(f"/api/v1/datasets/{ds['id']}/profile", headers=admin_headers)
    assert prof.status_code == 200, prof.text

    pack = client.get(f"/api/v1/datasets/{ds['id']}/monitor-pack", headers=admin_headers).json()
    checks = pack["managed_checks"]
    keys = {(c["check_type"], c["column_name"]) for c in checks}
    assert ("freshness", "created_at") in keys
    assert ("row_count_anomaly", None) in keys
    assert any(c["check_type"] == "distribution_drift" for c in checks)
    assert all(c["check_type"] in CHECK_TYPES for c in checks)
    assert all(c["origin"] == "system" and c["status"] == "active" for c in checks)
    assert all(c["params"]["monitor_pack"]["managed"] is True for c in checks)
    freshness = next(c for c in checks if c["check_type"] == "freshness")
    assert freshness["params"]["strategy"] == "adaptive"
    assert freshness["params"]["default_max_age_hours"] == freshness["params"]["max_age_hours"]
    assert {"min_history", "lookback_runs", "multiplier", "grace_hours"} <= set(freshness["params"])
    volume = next(c for c in checks if c["check_type"] == "row_count_anomaly")
    assert volume["params"]["strategy"] == "adaptive"
    assert "multiplier" in volume["params"]
    skipped = pack["reconciliation"]["skipped"]
    if "schema_contract" in CHECK_TYPES:
        assert ("schema_contract", None) in keys
        assert not any(
            s["kind"] == "schema" and s["code"] == "check_type_unavailable" for s in skipped
        )
        assert pack["reconciliation"]["status"] in {"ready", "partial"}
    else:
        assert pack["reconciliation"]["status"] == "partial"
        assert any(s["kind"] == "schema" and s["code"] == "check_type_unavailable" for s in skipped)


def test_reconcile_twice_is_idempotent(client, admin_headers, source_db):
    ds = _register_dataset(client, admin_headers, source_db)
    assert client.post(f"/api/v1/datasets/{ds['id']}/profile", headers=admin_headers).status_code == 200
    first = client.get(f"/api/v1/datasets/{ds['id']}/monitor-pack", headers=admin_headers).json()

    second_resp = client.post(
        f"/api/v1/datasets/{ds['id']}/monitor-pack/reconcile",
        headers=admin_headers,
    )
    assert second_resp.status_code == 200, second_resp.text
    second = second_resp.json()
    assert second["reconciliation"]["created"] == 0
    assert len(second["managed_checks"]) == len(first["managed_checks"])

    with session_factory()() as db:
        managed = [
            c
            for c in db.query(models.Check).filter(models.Check.dataset_id == ds["id"]).all()
            if (c.params or {}).get("monitor_pack", {}).get("managed") is True
        ]
        identities = {
            (c.params["monitor_pack"]["kind"], c.column_name or "")
            for c in managed
            if c.status != "archived"
        }
        assert len(identities) == len([c for c in managed if c.status != "archived"])


def test_disabling_pack_disables_only_managed_checks(client, admin_headers, source_db):
    ds = _register_dataset(client, admin_headers, source_db)
    assert client.post(f"/api/v1/datasets/{ds['id']}/profile", headers=admin_headers).status_code == 200
    manual = client.post(
        "/api/v1/checks",
        json={"dataset_id": ds["id"], "check_type": "not_null", "column_name": "email"},
        headers=admin_headers,
    )
    assert manual.status_code == 201, manual.text

    resp = client.patch(
        f"/api/v1/datasets/{ds['id']}/monitor-pack",
        json={"enabled": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    pack = resp.json()
    assert pack["enabled"] is False
    assert pack["status"] == "disabled"
    assert all(c["status"] == "disabled" for c in pack["managed_checks"])

    manual_after = client.get(f"/api/v1/checks?dataset_id={ds['id']}", headers=admin_headers).json()
    manual_check = next(c for c in manual_after if c["id"] == manual.json()["id"])
    assert manual_check["status"] == "active"


def test_reconcile_missing_profile_returns_status_payload(client, admin_headers, source_db):
    ds = _register_dataset(client, admin_headers, source_db)
    resp = client.post(
        f"/api/v1/datasets/{ds['id']}/monitor-pack/reconcile",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    pack = resp.json()
    assert pack["status"] == "pending_profile"
    assert pack["managed_checks"] == []
    assert {s["code"] for s in pack["reconciliation"]["skipped"]} == {"missing_profile"}
