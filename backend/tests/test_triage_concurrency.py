"""Triage optimistic concurrency (#156): when the client sends the versions it
last read, a stale client / concurrent triager is rejected with HTTP 409 instead
of silently clobbering analyst state.
"""


def _setup(client, admin_headers, source_db) -> tuple[dict, int]:
    h = admin_headers
    conn = client.post(
        "/api/v1/connections", json={"name": "triage-cc", "dsn": source_db}, headers=h
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]
    c = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds["id"], "check_type": "not_null", "column_name": "email",
            "severity": "error", "name": "triage-cc not null",
        },
        headers=h,
    ).json()
    client.post(f"/api/v1/checks/{c['id']}/run", headers=h)
    return h, c["id"]


def test_triage_optimistic_concurrency(client, admin_headers, source_db):
    h, check_id = _setup(client, admin_headers, source_db)

    page = client.get(f"/api/v1/exceptions?check_id={check_id}&limit=2", headers=h).json()
    items = page["items"]
    assert len(items) >= 2, "need two exceptions to test concurrency"
    ids = [it["id"] for it in items]
    versions = {it["id"]: it["version"] for it in items}
    assert all(v == 1 for v in versions.values())  # fresh exceptions start at version 1

    # A stale expected version on one id rejects the whole batch (409); nothing applied.
    stale = {**versions, ids[0]: versions[ids[0]] - 1}
    r = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": ids, "note": "cc probe", "expected_versions": stale},
        headers=h,
    )
    assert r.status_code == 409, r.text
    assert ids[0] in r.json()["detail"]["conflict_ids"]

    # The matching versions apply and bump each row's version.
    r2 = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": ids, "note": "cc probe", "expected_versions": versions},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    out = {x["id"]: x for x in r2.json()}
    for i in ids:
        assert out[i]["version"] == versions[i] + 1
        assert out[i]["note"] == "cc probe"

    # Re-submitting the now-stale versions conflicts again (the bump took effect).
    r3 = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": ids, "note": "again", "expected_versions": versions},
        headers=h,
    )
    assert r3.status_code == 409, r3.text

    # Without expected_versions, triage still applies (backward-compatible).
    r4 = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": ids, "status": "acknowledged"},
        headers=h,
    )
    assert r4.status_code == 200, r4.text
    assert all(x["status"] == "acknowledged" for x in r4.json())
