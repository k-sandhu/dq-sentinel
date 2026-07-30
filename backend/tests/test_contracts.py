from uuid import uuid4

import pytest


def _register_people(client, headers, source_db):
    suffix = uuid4().hex[:8]
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"contract-src-{suffix}", "dsn": source_db},
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


def test_contract_activate_materializes_checks_and_rolls_up_conformance(
    client, admin_headers, source_db
):
    ds = _register_people(client, admin_headers, source_db)
    spec = {
        "schema": {
            "columns": [
                {"name": "id", "dtype": "INTEGER", "required": True, "nullable": False},
                {"name": "email", "dtype": "TEXT", "required": True, "nullable": True},
                {"name": "created_at", "dtype": "TEXT", "required": True, "nullable": False},
                {"name": "optional_comment", "dtype": "TEXT", "required": False, "nullable": True},
            ],
            "allow_extra_columns": True,
        },
        "freshness": {"column": "created_at", "max_age_hours": 100, "severity": "error"},
        "volume": {"min_rows": 1, "severity": "warn"},
        "quality": [
            {
                "id": "email-not-null",
                "name": "Email populated",
                "check_type": "not_null",
                "column": "email",
                "severity": "error",
            }
        ],
        "owner": {"name": "data-platform", "importance": "high"},
        "consumers": [{"name": "support analytics"}],
    }
    created = client.post(
        f"/api/v1/datasets/{ds['id']}/contract",
        json={"name": "People contract", "version": "1.0.0", "spec": spec},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    contract = created.json()
    assert contract["status"] == "draft"
    assert contract["version_count"] == 1

    activated = client.post(
        f"/api/v1/datasets/{ds['id']}/contract/{contract['id']}/activate",
        headers=admin_headers,
    )
    assert activated.status_code == 200, activated.text
    body = activated.json()
    assert body["contract"]["status"] == "active"
    assert body["schema_pinned"] is True
    assert {c["check_type"] for c in body["created_checks"]} == {
        "schema_change",
        "freshness",
        "row_count_min",
        "not_null",
    }

    unknown = client.get(
        f"/api/v1/datasets/{ds['id']}/contract/{contract['id']}/conformance",
        headers=admin_headers,
    )
    assert unknown.status_code == 200, unknown.text
    unknown_body = unknown.json()
    assert unknown_body["status"] == "unknown"
    assert next(c for c in unknown_body["clauses"] if c["kind"] == "schema")["status"] == "pass"
    hist = client.get(f"/api/v1/datasets/{ds['id']}/schema-history", headers=admin_headers).json()
    pinned = next(s for s in hist["snapshots"] if s["id"] == hist["pinned_baseline_id"])
    assert {c["name"] for c in pinned["columns"]} == {"id", "email", "created_at"}

    for check in body["created_checks"]:
        run = client.post(f"/api/v1/checks/{check['id']}/run", headers=admin_headers)
        assert run.status_code == 200, run.text

    final = client.get(
        f"/api/v1/datasets/{ds['id']}/contract/conformance",
        headers=admin_headers,
    ).json()
    assert final["status"] == "breached"
    email_clause = next(c for c in final["clauses"] if c["clause_id"] == "quality:email-not-null")
    assert email_clause["status"] == "breached"
    assert email_clause["check_status"] == "fail"
    versions = client.get(
        f"/api/v1/datasets/{ds['id']}/contract/{contract['id']}/versions",
        headers=admin_headers,
    ).json()
    diff = client.get(
        f"/api/v1/datasets/{ds['id']}/contract/{contract['id']}/versions/{versions[-1]['id']}/diff"
        f"?to_version_id={versions[0]['id']}",
        headers=admin_headers,
    ).json()
    assert diff["added"]
    assert all(line.startswith("@@") for line in diff["changed"])


def test_contract_schema_break_and_odcs_round_trip(client, admin_headers, source_db):
    ds = _register_people(client, admin_headers, source_db)
    broken = client.post(
        f"/api/v1/datasets/{ds['id']}/contract",
        json={
            "name": "Broken schema",
            "version": "0.1.0",
            "spec": {
                "schema": {
                    "columns": [
                        {"name": "id", "dtype": "INTEGER", "required": True},
                        {"name": "missing_required", "dtype": "TEXT", "required": True},
                    ],
                    "allow_extra_columns": True,
                }
            },
        },
        headers=admin_headers,
    )
    assert broken.status_code == 201, broken.text
    conf = client.get(
        f"/api/v1/datasets/{ds['id']}/contract/{broken.json()['id']}/conformance",
        headers=admin_headers,
    ).json()
    assert conf["status"] == "breached"
    assert "missing_required" in conf["clauses"][0]["detail"]

    yaml_contract = """
apiVersion: v3.0.0
kind: DataContract
name: People ODCS
version: 2.0.0
schema:
  - name: id
    physicalType: INTEGER
    required: true
    nullable: false
  - name: email
    physicalType: TEXT
    required: true
    nullable: true
slaProperties:
  - property: freshness
    column: created_at
    threshold: PT48H
    severity: error
quality:
  - id: email-format
    name: Email format
    type: regex_match
    column: email
    severity: warn
    params:
      pattern: "^[^@]+@[^@]+\\\\.[^@]+$"
team:
  name: data-platform
stakeholders:
  - name: support analytics
terms: Supported subset round-trip
"""
    imported = client.post(
        f"/api/v1/datasets/{ds['id']}/contract/import",
        json={"yaml": yaml_contract},
        headers=admin_headers,
    )
    assert imported.status_code == 201, imported.text
    contract = imported.json()
    assert contract["name"] == "People ODCS"
    assert contract["version"] == "2.0.0"
    assert contract["spec"]["freshness"]["max_age_hours"] == 48
    assert contract["spec"]["quality"][0]["check_type"] == "regex_match"

    exported = client.get(
        f"/api/v1/datasets/{ds['id']}/contract/{contract['id']}/export?format=odcs",
        headers=admin_headers,
    )
    assert exported.status_code == 200, exported.text
    text = exported.json()["yaml"]
    assert "apiVersion: v3.0.0" in text
    assert "People ODCS" in text
    assert "email-format" in text


def test_contract_lifecycle_archives_materialized_checks(client, admin_headers, source_db):
    ds = _register_people(client, admin_headers, source_db)
    first = client.post(
        f"/api/v1/datasets/{ds['id']}/contract",
        json={
            "name": "First active contract",
            "version": "1.0.0",
            "status": "active",
            "spec": {
                "schema": {"columns": [{"name": "id", "dtype": "INTEGER", "required": True}]},
                "quality": [
                    {
                        "id": "email-populated",
                        "name": "Email populated",
                        "check_type": "not_null",
                        "column": "email",
                    }
                ],
            },
        },
        headers=admin_headers,
    )
    assert first.status_code == 201, first.text
    first_body = first.json()
    first_check_ids = {
        item["check_id"] for item in first_body["spec"]["materialized"]["checks"]
    }
    assert first_check_ids
    stale_quality_check_id = next(
        item["check_id"]
        for item in first_body["spec"]["materialized"]["checks"]
        if item["check_type"] == "not_null"
    )

    reapplied = client.patch(
        f"/api/v1/datasets/{ds['id']}/contract/{first_body['id']}",
        json={
            "status": "active",
            "spec": {
                "schema": {"columns": [{"name": "id", "dtype": "INTEGER", "required": True}]},
            },
        },
        headers=admin_headers,
    )
    assert reapplied.status_code == 200, reapplied.text
    reapplied_check_ids = {
        item["check_id"] for item in reapplied.json()["spec"]["materialized"]["checks"]
    }
    active_after_reapply = client.get(
        f"/api/v1/checks?dataset_id={ds['id']}&status=active",
        headers=admin_headers,
    ).json()
    active_after_reapply_ids = {c["id"] for c in active_after_reapply}
    assert stale_quality_check_id not in active_after_reapply_ids
    assert reapplied_check_ids <= active_after_reapply_ids

    second = client.post(
        f"/api/v1/datasets/{ds['id']}/contract",
        json={
            "name": "Replacement contract",
            "version": "2.0.0",
            "status": "active",
            "spec": {
                "schema": {"columns": [{"name": "id", "dtype": "INTEGER", "required": True}]},
                "volume": {"min_rows": 1},
            },
        },
        headers=admin_headers,
    )
    assert second.status_code == 201, second.text
    second_body = second.json()
    second_check_ids = {
        item["check_id"] for item in second_body["spec"]["materialized"]["checks"]
    }
    assert second_check_ids

    old = client.get(
        f"/api/v1/datasets/{ds['id']}/contract/{first_body['id']}",
        headers=admin_headers,
    ).json()
    assert old["status"] == "deprecated"

    active = client.get(
        f"/api/v1/checks?dataset_id={ds['id']}&status=active",
        headers=admin_headers,
    ).json()
    active_ids = {c["id"] for c in active}
    assert first_check_ids.isdisjoint(active_ids)
    assert second_check_ids <= active_ids

    deleted = client.delete(
        f"/api/v1/datasets/{ds['id']}/contract/{second_body['id']}",
        headers=admin_headers,
    )
    assert deleted.status_code == 204, deleted.text
    active_after_delete = client.get(
        f"/api/v1/checks?dataset_id={ds['id']}&status=active",
        headers=admin_headers,
    ).json()
    assert second_check_ids.isdisjoint({c["id"] for c in active_after_delete})


# --------------------------------------------------------------- clause markers (#306)
#
# A clause id is analyst-authored (``quality: [{"id": "order_total"}]``), and the
# contract finds the check it already materialized for a clause by LIKE-matching a
# marker embedded in the check's rationale. ``_`` and ``%`` are LIKE wildcards, so
# an unescaped marker matches a SIBLING clause's marker: the contract rebinds to
# the wrong check and then archives the right one — a contract edit silently stops
# the wrong monitor.
#
# The marker lookup is only reached when the spec carries no ``materialized`` block
# (a PATCH with a fresh spec drops it), which is why these tests activate, then
# re-apply the same spec.

_WILDCARD_CLAUSES = [
    # (clause id, column) — ids chosen so each one's LIKE pattern, unescaped,
    # also matches a sibling: "a_b" matches "axb" and "a%b"; "a%b" matches all.
    ("a_b", "email"),
    ("axb", "status"),
    ("a%b", "age"),
    ("axxb", "score"),
]


def _wildcard_spec() -> dict:
    return {
        "schema": {"columns": [{"name": "id", "dtype": "INTEGER", "required": True}]},
        "quality": [
            {
                "id": clause_id,
                "name": f"Populated {column}",
                "check_type": "not_null",
                "column": column,
            }
            for clause_id, column in _WILDCARD_CLAUSES
        ],
    }


def _clause_to_check(contract_body: dict) -> dict[str, int]:
    return {
        item["clause_id"]: item["check_id"]
        for item in contract_body["spec"]["materialized"]["checks"]
        if item["clause_id"].startswith("quality:")
    }


def test_clause_ids_with_like_wildcards_keep_their_own_check(client, admin_headers, source_db):
    """Re-applying a spec must rebind every clause to the check it already owns."""
    ds = _register_people(client, admin_headers, source_db)
    created = client.post(
        f"/api/v1/datasets/{ds['id']}/contract",
        json={
            "name": "Wildcard clause ids",
            "version": "1.0.0",
            "status": "active",
            "spec": _wildcard_spec(),
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    first = _clause_to_check(created.json())
    assert set(first) == {f"quality:{cid}" for cid, _ in _WILDCARD_CLAUSES}
    assert len(set(first.values())) == len(_WILDCARD_CLAUSES), "activation created shared checks"

    # A PATCH carries the analyst's spec, which has no `materialized` block — so
    # this re-activation resolves every clause through the marker LIKE.
    reapplied = client.patch(
        f"/api/v1/datasets/{ds['id']}/contract/{created.json()['id']}",
        json={"status": "active", "spec": _wildcard_spec()},
        headers=admin_headers,
    )
    assert reapplied.status_code == 200, reapplied.text
    second = _clause_to_check(reapplied.json())

    # The bug: "quality:a_b" matched the newest sibling marker instead of its own,
    # collapsing four clauses onto three checks and archiving the orphaned one.
    assert len(set(second.values())) == len(_WILDCARD_CLAUSES), (
        f"clauses collided onto shared checks: {second}"
    )
    assert second == first, "a clause was rebound to a sibling's check"

    active = client.get(
        f"/api/v1/checks?dataset_id={ds['id']}&status=active", headers=admin_headers
    ).json()
    active_ids = {c["id"] for c in active}
    assert set(first.values()) <= active_ids, "the contract archived a check it still owns"


@pytest.mark.parametrize(("wildcard_id", "sibling_id"), [("a_b", "axb"), ("a%b", "axxb")])
def test_deleting_a_contract_does_not_archive_a_sibling_clause_check(
    client, admin_headers, source_db, wildcard_id, sibling_id
):
    """Delete archives via the same marker match; a wildcard must not widen it."""
    ds = _register_people(client, admin_headers, source_db)
    spec = {
        "schema": {"columns": [{"name": "id", "dtype": "INTEGER", "required": True}]},
        "quality": [
            {"id": wildcard_id, "name": "W", "check_type": "not_null", "column": "email"},
            {"id": sibling_id, "name": "S", "check_type": "not_null", "column": "status"},
        ],
    }
    created = client.post(
        f"/api/v1/datasets/{ds['id']}/contract",
        json={"name": "Sibling clauses", "version": "1.0.0", "status": "active", "spec": spec},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    mapping = _clause_to_check(created.json())
    assert len(set(mapping.values())) == 2

    reapplied = client.patch(
        f"/api/v1/datasets/{ds['id']}/contract/{created.json()['id']}",
        json={"status": "active", "spec": spec},
        headers=admin_headers,
    )
    assert reapplied.status_code == 200, reapplied.text
    assert _clause_to_check(reapplied.json()) == mapping

    active = client.get(
        f"/api/v1/checks?dataset_id={ds['id']}&status=active", headers=admin_headers
    ).json()
    assert set(mapping.values()) <= {c["id"] for c in active}


# --------------------------------------------------- version diff scoping (#306, part 2)


def test_version_diff_rejects_a_version_from_another_contract(client, admin_headers, source_db):
    """Version ids are raw row ids: the diff must refuse ids outside the addressed
    contract, and must say 404 (not 403) so ids cannot be probed."""
    ds = _register_people(client, admin_headers, source_db)
    spec = {"schema": {"columns": [{"name": "id", "dtype": "INTEGER", "required": True}]}}

    def _make(name: str) -> dict:
        resp = client.post(
            f"/api/v1/datasets/{ds['id']}/contract",
            json={"name": name, "version": "1.0.0", "spec": spec},
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        contract_id = resp.json()["id"]
        versions = client.get(
            f"/api/v1/datasets/{ds['id']}/contract/{contract_id}/versions", headers=admin_headers
        ).json()
        assert versions, "a new contract snapshots version 1"
        return {"id": contract_id, "version_id": versions[0]["id"]}

    mine, theirs = _make("Diff scope A"), _make("Diff scope B")

    borrowed = client.get(
        f"/api/v1/datasets/{ds['id']}/contract/{mine['id']}/versions/{mine['version_id']}/diff"
        f"?to_version_id={theirs['version_id']}",
        headers=admin_headers,
    )
    assert borrowed.status_code == 404, borrowed.text
    assert borrowed.json()["detail"] == "Contract version not found"

    # ...and the same id pair the other way round.
    reversed_ = client.get(
        f"/api/v1/datasets/{ds['id']}/contract/{mine['id']}/versions/{theirs['version_id']}/diff"
        f"?to_version_id={mine['version_id']}",
        headers=admin_headers,
    )
    assert reversed_.status_code == 404, reversed_.text

    # The in-contract diff still works (the guard is scoping, not a blanket refusal).
    ok = client.get(
        f"/api/v1/datasets/{ds['id']}/contract/{mine['id']}/versions/{mine['version_id']}/diff"
        f"?to_version_id={mine['version_id']}",
        headers=admin_headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["contract_id"] == mine["id"]
