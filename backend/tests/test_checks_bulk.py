"""POST /checks/bulk-transition — conditional bulk activate/dismiss of proposals
(review remediation on the proposal-wall PR): per-id outcomes, no clobbering of
rows that changed while the reviewer looked at the list."""

import pytest


@pytest.fixture(scope="module")
def seeded(client, admin_headers, source_db):
    h = admin_headers
    conn = client.post(
        "/api/v1/connections", json={"name": "bulktrans-src", "dsn": source_db}, headers=h
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]

    def mk(name, status="proposed"):
        r = client.post(
            "/api/v1/checks",
            json={
                "dataset_id": ds["id"], "check_type": "not_null", "column_name": "email",
                "severity": "warn", "name": name, "status": status,
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        return r.json()

    return {
        "h": h,
        "p1": mk("bulk p1"),
        "p2": mk("bulk p2"),
        "p3": mk("bulk p3"),
        "already_active": mk("bulk active", status="active"),
    }


def _status_of(client, h, check_id):
    return client.get(f"/api/v1/checks/{check_id}/versions", headers=h)  # existence probe


def test_bulk_activate_is_conditional_with_per_id_outcomes(client, seeded):
    h = seeded["h"]
    ids = [seeded["p1"]["id"], seeded["p2"]["id"], seeded["already_active"]["id"], 99999999]
    r = client.post(
        "/api/v1/checks/bulk-transition", json={"ids": ids, "action": "activate"}, headers=h
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["activated"] == 2
    assert body["skipped_not_proposed"] == 1  # the already-active row is untouched
    assert body["not_found"] == 1
    assert body["outcomes"][str(seeded["p1"]["id"])] == "activated"
    assert body["outcomes"][str(seeded["already_active"]["id"])] == "skipped_not_proposed"

    checks = {c["id"]: c for c in client.get("/api/v1/checks", headers=h).json()}
    assert checks[seeded["p1"]["id"]]["status"] == "active"
    assert checks[seeded["p2"]["id"]]["status"] == "active"


def test_bulk_dismiss_archives_only_proposals(client, seeded):
    h = seeded["h"]
    # p1 was activated above — dismissing it in bulk must be a no-op skip, not an
    # archive of a live check (the race codex flagged on the chunked client loop).
    ids = [seeded["p3"]["id"], seeded["p1"]["id"]]
    r = client.post(
        "/api/v1/checks/bulk-transition", json={"ids": ids, "action": "dismiss"}, headers=h
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dismissed"] == 1
    assert body["skipped_not_proposed"] == 1

    checks = {c["id"]: c for c in client.get("/api/v1/checks", headers=h).json()}
    assert seeded["p3"]["id"] not in checks  # archived rows leave the list
    assert checks[seeded["p1"]["id"]]["status"] == "active"  # untouched


def test_bulk_rejects_empty_and_oversized(client, seeded):
    h = seeded["h"]
    assert client.post(
        "/api/v1/checks/bulk-transition", json={"ids": [], "action": "activate"}, headers=h
    ).status_code == 422
    assert client.post(
        "/api/v1/checks/bulk-transition",
        json={"ids": list(range(1, 502)), "action": "activate"},
        headers=h,
    ).status_code == 422
