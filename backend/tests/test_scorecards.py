from datetime import date, timedelta
from uuid import uuid4

from app import models
from app.core.scorecards import capture_scorecard_snapshots
from app.db import init_db, session_factory
from app.models import utcnow


def _metadata_dataset(db, *, suffix: str):
    conn = models.Connection(
        name=f"scorecards-{suffix}",
        kind="sqlite",
        dsn="sqlite:///metadata-only.sqlite",
    )
    db.add(conn)
    db.flush()
    dataset = models.Dataset(
        connection_id=conn.id,
        schema_name=f"domain_{suffix}",
        table_name="orders",
        display_name="Orders",
        exploration={"team": f"Revenue Ops {suffix}"},
    )
    db.add(dataset)
    db.flush()
    knowledge = models.TableKnowledge(
        dataset_id=dataset.id,
        importance="critical",
        owner=f"owner-{suffix}",
    )
    passing = models.Check(
        dataset_id=dataset.id,
        name="passing",
        check_type="not_null",
        column_name="id",
        status="active",
        severity="error",
        last_status="pass",
    )
    failing = models.Check(
        dataset_id=dataset.id,
        name="failing",
        check_type="not_null",
        column_name="email",
        status="active",
        severity="error",
        last_status="fail",
    )
    db.add_all([knowledge, passing, failing])
    db.flush()
    run = models.CheckRun(
        check_id=failing.id,
        dataset_id=dataset.id,
        status="fail",
        violation_count=1,
        metrics={},
    )
    db.add(run)
    db.flush()
    exc = models.ExceptionRecord(
        run_id=run.id,
        check_id=failing.id,
        dataset_id=dataset.id,
        row_data={"email": "redacted@example.com"},
        reason="metadata-only fixture",
        status="open",
    )
    sla = models.SLADefinition(
        name="quality target",
        scope="dataset",
        scope_id=dataset.id,
        target_type="check_success",
        objective=0.95,
        enabled=True,
    )
    db.add_all([exc, sla])
    db.commit()
    return dataset.id, failing.id, exc.id


def test_capture_scorecard_snapshots_is_idempotent_metadata_only():
    init_db()
    suffix = uuid4().hex[:8]
    day = date(2026, 1, 23)
    factory = session_factory()
    with factory() as db:
        dataset_id, failing_id, exc_id = _metadata_dataset(db, suffix=suffix)
        first = capture_scorecard_snapshots(db, day)
        db.commit()
        first_count = (
            db.query(models.ScorecardSnapshot)
            .filter(models.ScorecardSnapshot.snapshot_date == day)
            .count()
        )
        assert first_count == len(first)

        owner_key = f"owner-{suffix}"
        owner_snap = (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "owner",
                models.ScorecardSnapshot.key == owner_key,
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .one()
        )
        assert owner_snap.dataset_count == 1
        assert owner_snap.active_check_count == 2
        assert owner_snap.open_exception_count == 1
        assert owner_snap.breached_dataset_count == 1
        assert owner_snap.score == 50.0
        assert owner_snap.slo_target == 95.0
        assert owner_snap.slo_status == "breached"
        detail_text = str(owner_snap.detail).lower()
        assert "sqlite:///" not in detail_text
        assert "select " not in detail_text
        assert "redacted@example.com" not in detail_text

        assert (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "dataset",
                models.ScorecardSnapshot.key == str(dataset_id),
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .count()
            == 1
        )
        assert (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "domain",
                models.ScorecardSnapshot.key == f"domain_{suffix}",
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .count()
            == 1
        )
        assert (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "team",
                models.ScorecardSnapshot.key == f"Revenue Ops {suffix}",
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .count()
            == 1
        )
        assert (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "importance",
                models.ScorecardSnapshot.key == "critical",
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .count()
            >= 1
        )

        db.get(models.Check, failing_id).last_status = "pass"
        db.get(models.ExceptionRecord, exc_id).status = "resolved"
        second = capture_scorecard_snapshots(db, day)
        db.commit()

        assert (
            db.query(models.ScorecardSnapshot)
            .filter(models.ScorecardSnapshot.snapshot_date == day)
            .count()
            == first_count
        )
        assert len(second) == len(first)
        db.refresh(owner_snap)
        assert owner_snap.score == 100.0
        assert owner_snap.open_exception_count == 0
        assert owner_snap.breached_dataset_count == 0
        assert owner_snap.slo_status == "met"


def test_scorecard_history_requires_auth_and_returns_sparse_ordered_points(client, admin_headers):
    key = f"hist-{uuid4().hex[:8]}"
    today = utcnow().date()
    with session_factory()() as db:
        db.add_all(
            [
                models.ScorecardSnapshot(
                    grain="owner",
                    key=key,
                    label="History owner",
                    snapshot_date=today - timedelta(days=2),
                    score=91.0,
                    slo_target=95.0,
                    slo_status="breached",
                    dataset_count=1,
                    active_check_count=2,
                    open_exception_count=1,
                    breached_dataset_count=1,
                    detail={"schema_version": 1},
                ),
                models.ScorecardSnapshot(
                    grain="owner",
                    key=key,
                    label="History owner",
                    snapshot_date=today,
                    score=99.0,
                    slo_target=95.0,
                    slo_status="met",
                    dataset_count=1,
                    active_check_count=2,
                    open_exception_count=0,
                    breached_dataset_count=0,
                    detail={"schema_version": 1},
                ),
            ]
        )
        db.commit()

    unauth = client.get(f"/api/v1/scorecards/history?grain=owner&key={key}&days=3")
    assert unauth.status_code == 401
    resp = client.get(
        f"/api/v1/scorecards/history?grain=owner&key={key}&days=3",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["grain"] == "owner"
    assert body["key"] == key
    assert body["sparse"] is True
    assert [p["snapshot_date"] for p in body["points"]] == [
        (today - timedelta(days=2)).isoformat(),
        today.isoformat(),
    ]
    assert [p["score"] for p in body["points"]] == [91.0, 99.0]


def test_dataset_delete_removes_dataset_grain_snapshots_only(client, admin_headers, source_db):
    suffix = uuid4().hex[:8]
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"scorecard-delete-{suffix}", "dsn": source_db},
        headers=admin_headers,
    ).json()
    dataset = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=admin_headers,
    ).json()[0]
    dataset_id = dataset["id"]
    owner_key = f"retained-owner-{suffix}"
    today = utcnow().date()

    with session_factory()() as db:
        db.add_all(
            [
                models.ScorecardSnapshot(
                    grain="dataset",
                    key=str(dataset_id),
                    label="People",
                    snapshot_date=today,
                    score=100.0,
                    slo_status="unknown",
                    dataset_count=1,
                    active_check_count=0,
                    open_exception_count=0,
                    breached_dataset_count=0,
                    detail={"schema_version": 1},
                ),
                models.ScorecardSnapshot(
                    grain="owner",
                    key=owner_key,
                    label="Retained owner",
                    snapshot_date=today,
                    score=100.0,
                    slo_status="unknown",
                    dataset_count=1,
                    active_check_count=0,
                    open_exception_count=0,
                    breached_dataset_count=0,
                    detail={"schema_version": 1},
                ),
            ]
        )
        db.commit()

    resp = client.delete(f"/api/v1/datasets/{dataset_id}", headers=admin_headers)
    assert resp.status_code == 204, resp.text

    with session_factory()() as db:
        assert (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "dataset",
                models.ScorecardSnapshot.key == str(dataset_id),
            )
            .count()
            == 0
        )
        assert (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "owner",
                models.ScorecardSnapshot.key == owner_key,
            )
            .count()
            == 1
        )
