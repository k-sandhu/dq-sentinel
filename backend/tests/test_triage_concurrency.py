"""Triage optimistic concurrency (#156): when the client sends the versions it
last read, a stale client / concurrent triager is rejected with HTTP 409 instead
of silently clobbering analyst state.
"""

import uuid


def _setup(client, admin_headers, source_db) -> tuple[dict, int]:
    h = admin_headers
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"triage-cc-{uuid.uuid4().hex[:8]}", "dsn": source_db},
        headers=h,
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


def test_triage_partial_expected_versions_rejected(client, admin_headers, source_db):
    """A present-but-incomplete expected_versions map is a fail-open shape — it
    must be rejected (422) rather than silently leaving the omitted row unguarded.
    """
    h, check_id = _setup(client, admin_headers, source_db)
    items = client.get(f"/api/v1/exceptions?check_id={check_id}&limit=2", headers=h).json()["items"]
    ids = [it["id"] for it in items]
    r = client.post(
        "/api/v1/exceptions/triage",
        json={
            "ids": ids,
            "status": "acknowledged",
            "expected_versions": {ids[0]: items[0]["version"]},  # ids[1] omitted
        },
        headers=h,
    )
    assert r.status_code == 422, r.text
    assert ids[1] in r.json()["detail"]["missing_ids"]


def test_comment_bumps_version_and_blocks_stale_triage(client, admin_headers, source_db):
    """A standalone comment mutates note, so it must bump version and (optionally)
    enforce its own optimistic-concurrency check — otherwise a stale triager
    clobbers the latest note.
    """
    h, check_id = _setup(client, admin_headers, source_db)
    item = client.get(f"/api/v1/exceptions?check_id={check_id}&limit=1", headers=h).json()["items"][0]
    eid, v = item["id"], item["version"]

    rc = client.post(f"/api/v1/exceptions/{eid}/comments", json={"comment": "looking into it"}, headers=h)
    assert rc.status_code == 201, rc.text
    after = client.get(f"/api/v1/exceptions?check_id={check_id}&limit=1", headers=h).json()["items"][0]
    assert after["version"] == v + 1  # comment bumped the version
    assert after["note"] == "looking into it"

    # A triage holding the pre-comment version is now stale -> 409.
    r = client.post(
        "/api/v1/exceptions/triage",
        json={"ids": [eid], "status": "resolved", "expected_versions": {eid: v}},
        headers=h,
    )
    assert r.status_code == 409, r.text

    # The comment endpoint itself enforces optimistic concurrency when asked.
    rc2 = client.post(
        f"/api/v1/exceptions/{eid}/comments",
        json={"comment": "stale", "expected_version": v},
        headers=h,
    )
    assert rc2.status_code == 409, rc2.text
