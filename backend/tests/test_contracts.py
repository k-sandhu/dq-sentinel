from uuid import uuid4


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
