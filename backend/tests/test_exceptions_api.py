"""Exceptions API v2: filters, facets, paged envelope, CSV export (#57)."""

import csv
import io
import json

import pytest

from app.db import session_factory
from app.models import ExceptionRecord, utcnow


def _audit(client, headers, **params):
    resp = client.get("/api/v1/audit", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture(scope="module")
def seeded(client, admin_headers, source_db):
    """Two checks of different severity/type producing exceptions; plus an
    assigned + a synthetically-recurring record for filter coverage."""
    h = admin_headers
    conn = client.post(
        "/api/v1/connections", json={"name": "ev2-src", "dsn": source_db}, headers=h
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]

    # error-severity not_null on email -> 5 exceptions
    c_err = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds["id"], "check_type": "not_null", "column_name": "email",
            "severity": "error", "name": "ev2 email not null",
        },
        headers=h,
    ).json()
    run_err = client.post(f"/api/v1/checks/{c_err['id']}/run", headers=h).json()

    # warn-severity accepted_values on status -> 1 exception
    c_warn = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds["id"], "check_type": "accepted_values", "column_name": "status",
            "params": {"values": ["active", "inactive"]}, "severity": "warn",
            "name": "ev2 status values",
        },
        headers=h,
    ).json()
    client.post(f"/api/v1/checks/{c_warn['id']}/run", headers=h)

    # An assignee.
    assignees = client.get("/api/v1/auth/assignees", headers=h).json()
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert any(a["id"] == me["id"] for a in assignees)

    # Synthetically mark one error-exception as recurring + assigned to admin,
    # and another with an injection-y reason for the CSV test.
    factory = session_factory()
    with factory() as db:
        recs = (
            db.query(ExceptionRecord)
            .filter(ExceptionRecord.check_id == c_err["id"])
            .order_by(ExceptionRecord.id)
            .all()
        )
        recs[0].occurrence_count = 5
        recs[0].assigned_to_id = me["id"]
        recs[0].last_seen_at = utcnow()
        recs[1].reason = "=HYPERLINK(\"http://evil\")"  # CSV injection probe
        db.commit()

    return {
        "h": h,
        "dataset_id": ds["id"],
        "check_err": c_err["id"],
        "check_warn": c_warn["id"],
        "run_err": run_err["id"],
        "me": me["id"],
    }


def test_envelope_paging(client, seeded):
    h = seeded["h"]
    p1 = client.get(
        f"/api/v1/exceptions?check_id={seeded['check_err']}&limit=2&offset=0", headers=h
    ).json()
    assert p1["limit"] == 2 and p1["offset"] == 0
    assert p1["total"] == 5
    assert len(p1["items"]) == 2
    p2 = client.get(
        f"/api/v1/exceptions?check_id={seeded['check_err']}&limit=2&offset=2", headers=h
    ).json()
    # Deterministic, non-overlapping pages.
    assert {e["id"] for e in p1["items"]}.isdisjoint({e["id"] for e in p2["items"]})


def test_filter_severity_via_join(client, seeded):
    h = seeded["h"]
    warn = client.get(f"/api/v1/exceptions?severity=warn&dataset_id={seeded['dataset_id']}", headers=h).json()
    assert warn["total"] == 1
    err = client.get(f"/api/v1/exceptions?severity=error&dataset_id={seeded['dataset_id']}", headers=h).json()
    assert err["total"] == 5


def test_filter_multi_status(client, seeded):
    h = seeded["h"]
    # Acknowledge two of the error exceptions.
    ids = [e["id"] for e in client.get(
        f"/api/v1/exceptions?check_id={seeded['check_err']}", headers=h
    ).json()["items"][:2]]
    client.post(
        "/api/v1/exceptions/triage", json={"ids": ids, "status": "acknowledged"}, headers=h
    )
    both = client.get(
        f"/api/v1/exceptions?check_id={seeded['check_err']}&status=open&status=acknowledged",
        headers=h,
    ).json()
    assert both["total"] == 5  # all of them, across the two statuses
    only_ack = client.get(
        f"/api/v1/exceptions?check_id={seeded['check_err']}&status=acknowledged", headers=h
    ).json()
    assert only_ack["total"] == 2


def test_filter_assignee_me_and_none(client, seeded):
    h = seeded["h"]
    mine = client.get("/api/v1/exceptions?assignee=me", headers=h).json()
    assert mine["total"] >= 1
    assert all(e["assigned_to_id"] == seeded["me"] for e in mine["items"])
    none = client.get(
        f"/api/v1/exceptions?assignee=none&check_id={seeded['check_err']}", headers=h
    ).json()
    assert all(e["assigned_to_id"] is None for e in none["items"])


def test_filter_recurrence(client, seeded):
    h = seeded["h"]
    recurring = client.get(
        f"/api/v1/exceptions?recurrence=recurring&check_id={seeded['check_err']}", headers=h
    ).json()
    assert recurring["total"] == 1  # the one we bumped to occurrence_count=5
    assert recurring["items"][0]["occurrence_count"] >= 2
    new = client.get(
        f"/api/v1/exceptions?recurrence=new&check_id={seeded['check_err']}", headers=h
    ).json()
    assert new["total"] >= 1  # freshly created within 24h


def test_filter_q_on_check_name(client, seeded):
    h = seeded["h"]
    hit = client.get(f"/api/v1/exceptions?q=status+values&dataset_id={seeded['dataset_id']}", headers=h).json()
    assert hit["total"] == 1  # matched on Check.name


def test_sort_occurrences_and_severity(client, seeded):
    h = seeded["h"]
    occ = client.get(
        f"/api/v1/exceptions?check_id={seeded['check_err']}&sort=occurrences", headers=h
    ).json()
    assert occ["items"][0]["occurrence_count"] == 5  # highest first

    sev = client.get(
        f"/api/v1/exceptions?dataset_id={seeded['dataset_id']}&sort=severity&limit=500", headers=h
    ).json()
    # error (0) precedes warn (1).
    sevs = [e["check_type"] for e in sev["items"]]
    # The single warn (accepted_values) must come after all error rows (not_null).
    assert sevs.index("accepted_values") == len(sevs) - 1


def test_facets_exclude_own_dimension(client, seeded):
    h = seeded["h"]
    # Filtering status=open must still report counts for OTHER statuses.
    facets = client.get(
        f"/api/v1/exceptions/facets?dataset_id={seeded['dataset_id']}&status=open", headers=h
    ).json()
    # acknowledged set earlier still appears in the status facet despite status=open filter.
    assert "acknowledged" in facets["status"]
    assert facets["status"].get("acknowledged", 0) >= 1
    # Severity + check_type + datasets facets present.
    assert set(facets["severity"]) >= {"error", "warn"}
    assert facets["datasets"] and facets["datasets"][0]["count"] >= 1
    # total honors the active filter (status=open -> excludes acknowledged).
    open_total = client.get(
        f"/api/v1/exceptions?dataset_id={seeded['dataset_id']}&status=open", headers=h
    ).json()["total"]
    assert facets["total"] == open_total


def test_export_csv(client, seeded):
    h = seeded["h"]
    resp = client.get(f"/api/v1/exceptions/export.csv?dataset_id={seeded['dataset_id']}", headers=h)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=exceptions.csv" in resp.headers["content-disposition"]

    reader = list(csv.reader(io.StringIO(resp.text)))
    header = reader[0]
    assert header[:3] == ["id", "dataset", "check"]
    assert "row_data" in header
    body = reader[1:]
    assert len(body) == 6  # 5 error + 1 warn, all under the cap

    # row_data column is valid JSON per cell.
    rd_idx = header.index("row_data")
    for row in body:
        json.loads(row[rd_idx])

    # CSV injection neutralized: the =HYPERLINK reason comes out prefixed with '.
    reason_idx = header.index("reason")
    assert any(r[reason_idx].startswith("'=HYPERLINK") for r in body)


def test_export_respects_filters(client, seeded):
    h = seeded["h"]
    resp = client.get(
        f"/api/v1/exceptions/export.csv?severity=warn&dataset_id={seeded['dataset_id']}", headers=h
    )
    rows = list(csv.reader(io.StringIO(resp.text)))[1:]
    assert len(rows) == 1  # only the warn exception


def test_export_csv_writes_safe_audit(client, seeded):
    h = seeded["h"]
    sentinel = "AUDIT_EXPORT_ROW_SECRET_125"
    factory = session_factory()
    with factory() as db:
        rec = (
            db.query(ExceptionRecord)
            .filter(ExceptionRecord.dataset_id == seeded["dataset_id"])
            .order_by(ExceptionRecord.id)
            .first()
        )
        assert rec is not None
        rec.row_data = {**(rec.row_data or {}), "audit_secret": sentinel}
        db.commit()

    before = _audit(client, h, action="exception.export")["total"]
    resp = client.get(
        f"/api/v1/exceptions/export.csv?dataset_id={seeded['dataset_id']}&severity=error&sort=severity",
        headers=h,
    )
    assert resp.status_code == 200

    after = _audit(client, h, action="exception.export")
    assert after["total"] == before + 1
    row = after["items"][0]
    assert row["entity_type"] == "exception"
    assert row["entity_id"] is None

    detail = row["detail"]
    assert detail["filters"] == {"dataset_id": seeded["dataset_id"], "severity": ["error"]}
    assert detail["sort"] == "severity"
    assert detail["matching_count"] == 5
    assert detail["exported_count"] == 5
    assert detail["export_cap"] == 10_000
    assert detail["truncated"] is False

    detail_blob = json.dumps(detail, sort_keys=True)
    assert sentinel not in detail_blob
    assert "row_data" not in detail_blob
    assert "sqlite:///" not in detail_blob
    assert "dsn" not in detail_blob.lower()


def test_export_csv_audit_records_truncation(client, seeded, monkeypatch):
    import app.api.exceptions_api as exceptions_api

    h = seeded["h"]
    monkeypatch.setattr(exceptions_api, "EXPORT_CAP", 2)

    before = _audit(client, h, action="exception.export")["total"]
    resp = client.get(f"/api/v1/exceptions/export.csv?dataset_id={seeded['dataset_id']}", headers=h)
    assert resp.status_code == 200
    assert len(list(csv.reader(io.StringIO(resp.text)))) == 3  # header + capped rows

    after = _audit(client, h, action="exception.export")
    assert after["total"] == before + 1
    detail = after["items"][0]["detail"]
    assert detail["filters"] == {"dataset_id": seeded["dataset_id"]}
    assert detail["matching_count"] == 6
    assert detail["exported_count"] == 2
    assert detail["export_cap"] == 2
    assert detail["truncated"] is True
