from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import models
from app.core import scorecards
from app.db import init_db, session_factory
from app.models import Check, CheckRun, Connection, Dataset, ExceptionRecord, TableKnowledge, utcnow


def _obj(**kwargs):
    return SimpleNamespace(**kwargs)


def _dataset(dataset_id: int = 1, name: str = "orders"):
    return _obj(id=dataset_id, table_name=name, display_name=name, schema_name=None)


def _check(check_id: int, *, severity: str = "error", last_status: str | None = None):
    return _obj(id=check_id, status="active", severity=severity, last_status=last_status)


def test_dataset_score_uses_severity_weights_and_latest_statuses():
    scored = scorecards.score_dataset(
        _dataset(),
        _obj(importance="medium"),
        [
            _check(1, severity="info"),
            _check(2, severity="warn"),
            _check(3, severity="error"),
        ],
        {1: "pass", 2: "warn", 3: "fail"},
    )

    assert scored.base_score == 34.29
    assert scored.score == 34.29
    assert scored.passing_checks == 1
    assert scored.warning_checks == 1
    assert scored.failing_checks == 1
    assert scored.error_checks == 0
    assert scored.unknown_checks == 0
    assert scored.slo_target == 90
    assert scored.slo_status == "breached"


def test_missing_runs_score_as_unknown_and_no_active_checks_are_unknown():
    missing = scorecards.score_dataset(
        _dataset(),
        _obj(importance="low"),
        [_check(1, severity="error")],
        {},
    )
    assert missing.score == 50
    assert missing.unknown_checks == 1
    assert missing.slo_target == 85

    empty = scorecards.score_dataset(_dataset(), _obj(importance="critical"), [], {})
    assert empty.score is None
    assert empty.base_score is None
    assert empty.slo_target == 98
    assert empty.slo_status == "unknown"
    assert empty.active_checks == 0


def test_exception_pressure_penalty_is_capped():
    scored = scorecards.score_dataset(
        _dataset(),
        _obj(importance="low"),
        [_check(1, severity="error")],
        {1: "pass"},
        open_exception_count=100,
    )

    assert scored.base_score == 100
    assert scored.exception_penalty == scorecards.MAX_EXCEPTION_PENALTY
    assert scored.score == 70


def test_importance_weighted_rollups_and_unassigned_domain_team_buckets():
    critical = scorecards.score_dataset(
        _dataset(1, "orders"),
        _obj(importance="critical", domain="", team=""),
        [_check(1)],
        {1: "pass"},
    )
    low = scorecards.score_dataset(
        _dataset(2, "events"),
        _obj(importance="low", domain="Marketing", team="Growth"),
        [_check(2)],
        {2: "fail"},
    )

    global_score = scorecards.aggregate_scores([critical, low])
    assert global_score.score == 80

    domain_rollups = scorecards.rollup_scores([critical, low], "domain")
    unassigned = next(r for r in domain_rollups if r.key == "")
    assert unassigned.label == scorecards.UNASSIGNED_LABEL
    assert unassigned.total_datasets == 1

    team_rollups = scorecards.rollup_scores([critical, low], "team")
    assert any(r.key == "" and r.label == scorecards.UNASSIGNED_LABEL for r in team_rollups)


def test_explicit_and_disabled_slo_fields_are_getattr_compatible():
    explicit = scorecards.score_dataset(
        _dataset(),
        _obj(importance="low", slo_target_score=99.0),
        [_check(1)],
        {1: "pass"},
    )
    assert explicit.slo_target == 99
    assert explicit.slo_target_source == "explicit"
    assert explicit.slo_status == "met"
    assert explicit.score_gap == 1

    disabled = scorecards.score_dataset(
        _dataset(),
        _obj(importance="critical", slo_enabled=False, slo_target_score=99.0),
        [_check(1)],
        {1: "fail"},
    )
    assert disabled.slo_target is None
    assert disabled.slo_target_source == "disabled"
    assert disabled.slo_status == "disabled"
    assert disabled.score_gap is None


def _insert_scorecard_fixture() -> int:
    init_db()
    suffix = uuid4().hex[:8]
    with session_factory()() as db:
        conn = Connection(name=f"scorecard-src-{suffix}", kind="sqlite", dsn="sqlite:///:memory:")
        db.add(conn)
        db.flush()
        dataset = Dataset(
            connection_id=conn.id,
            table_name=f"scorecard_orders_{suffix}",
            display_name=f"Scorecard Orders {suffix}",
        )
        db.add(dataset)
        db.flush()
        db.add(
            TableKnowledge(
                dataset_id=dataset.id,
                importance="critical",
                owner="Data Quality",
                domain="Commerce",
                team="Reliability",
            )
        )
        check = Check(
            dataset_id=dataset.id,
            name="orders not null",
            check_type="not_null",
            column_name="order_id",
            params={},
            severity="error",
            status="active",
            last_status="pass",
        )
        db.add(check)
        db.flush()
        run = CheckRun(
            check_id=check.id,
            dataset_id=dataset.id,
            status="pass",
            violation_count=0,
            metrics={},
        )
        db.add(run)
        db.flush()
        for status in ("open", "acknowledged", "expected", "resolved", "muted"):
            db.add(
                ExceptionRecord(
                    run_id=run.id,
                    check_id=check.id,
                    dataset_id=dataset.id,
                    row_data={},
                    status=status,
                )
            )
        db.commit()
        return dataset.id


def test_scorecard_endpoints_require_auth(client):
    assert client.get("/api/v1/scorecards/summary").status_code == 401
    assert client.get("/api/v1/scorecards/rollups").status_code == 401
    assert client.get("/api/v1/scorecards/datasets").status_code == 401
    assert client.get("/api/v1/scorecards/history").status_code == 401


def test_scorecard_api_shape_and_exception_status_filtering(client, admin_headers):
    dataset_id = _insert_scorecard_fixture()

    summary = client.get("/api/v1/scorecards/summary", headers=admin_headers)
    assert summary.status_code == 200
    body = summary.json()
    assert {"score", "slo_status", "worst_rollups", "top_failing_datasets"} <= set(body)

    rows = client.get("/api/v1/scorecards/datasets?limit=500", headers=admin_headers)
    assert rows.status_code == 200
    page = rows.json()
    item = next(row for row in page["items"] if row["dataset_id"] == dataset_id)
    assert item["open_exceptions"] == 1
    assert item["exception_penalty"] == 2
    assert item["score_drivers"]["open_exceptions"] == 1
    assert item["score"] == 98
    assert item["slo_status"] == "met"
    assert "score_drivers" in item
    assert "dsn" not in item

    rollups = client.get("/api/v1/scorecards/rollups?dimension=domain", headers=admin_headers)
    assert rollups.status_code == 200
    assert any(row["key"] == "Commerce" and row["label"] == "Commerce" for row in rollups.json())


@pytest.mark.parametrize("dimension", ["domain", "team", "owner", "importance"])
def test_scorecard_rollup_dimensions(client, admin_headers, dimension):
    resp = client.get(f"/api/v1/scorecards/rollups?dimension={dimension}", headers=admin_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_snapshot_capture_matches_live_scorecard_scoring_and_is_idempotent():
    init_db()
    suffix = uuid4().hex[:8]
    day = date(2026, 2, 17)
    with session_factory()() as db:
        conn = models.Connection(
            name=f"scorecard-history-{suffix}",
            kind="sqlite",
            dsn="sqlite:///metadata-only.sqlite",
        )
        db.add(conn)
        db.flush()
        dataset = models.Dataset(
            connection_id=conn.id,
            schema_name=f"schema_not_domain_{suffix}",
            table_name="orders",
            display_name="Orders",
            exploration={"team": f"wrong-team-{suffix}"},
        )
        db.add(dataset)
        db.flush()
        knowledge = models.TableKnowledge(
            dataset_id=dataset.id,
            importance="high",
            owner=f"owner-{suffix}",
            domain=f"domain-{suffix}",
            team=f"team-{suffix}",
            slo_target_score=80.0,
            slo_enabled=True,
        )
        checks = [
            models.Check(
                dataset_id=dataset.id,
                name="info pass",
                check_type="not_null",
                column_name="id",
                status="active",
                severity="info",
                last_status="pass",
            ),
            models.Check(
                dataset_id=dataset.id,
                name="warn warning",
                check_type="not_null",
                column_name="email",
                status="active",
                severity="warn",
                last_status="warn",
            ),
            models.Check(
                dataset_id=dataset.id,
                name="error fail",
                check_type="not_null",
                column_name="amount",
                status="active",
                severity="error",
                last_status="fail",
            ),
        ]
        db.add_all([knowledge, *checks])
        db.flush()
        for check, status in zip(checks, ("pass", "warn", "fail"), strict=True):
            db.add(
                models.CheckRun(
                    check_id=check.id,
                    dataset_id=dataset.id,
                    status=status,
                    violation_count=0 if status != "fail" else 3,
                    metrics={},
                )
            )
        db.flush()
        active_run = (
            db.query(models.CheckRun).filter(models.CheckRun.check_id == checks[2].id).one()
        )
        open_exceptions = []
        for status in ("open", "acknowledged", "expected", "resolved"):
            exc = models.ExceptionRecord(
                run_id=active_run.id,
                check_id=checks[2].id,
                dataset_id=dataset.id,
                row_data={"email": "sample@example.com"},
                reason="metadata-only fixture",
                status=status,
            )
            db.add(exc)
            if status == scorecards.OPEN_EXCEPTION_STATUS:
                open_exceptions.append(exc)
        db.commit()

        snapshots = scorecards.capture_scorecard_snapshots(db, day)
        db.commit()
        live_score = next(s for s in scorecards.load_dataset_scores(db) if s.dataset_id == dataset.id)

        assert live_score.base_score == 34.29
        assert live_score.exception_penalty == 2.0
        assert live_score.score == 32.29
        assert live_score.slo_target == 80.0
        assert live_score.slo_target_source == "explicit"
        assert live_score.slo_status == "breached"
        assert live_score.open_exceptions == 1

        dataset_snap = (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "dataset",
                models.ScorecardSnapshot.key == str(dataset.id),
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .one()
        )
        assert dataset_snap.score == live_score.score
        assert dataset_snap.slo_target == live_score.slo_target
        assert dataset_snap.slo_status == live_score.slo_status
        assert dataset_snap.open_exception_count == live_score.open_exceptions
        assert dataset_snap.detail["base_score"] == live_score.base_score
        assert dataset_snap.detail["exception_penalty"] == live_score.exception_penalty
        assert dataset_snap.detail["score_drivers"]["severity_weight_total"] == 3.5
        assert "sample@example.com" not in str(dataset_snap.detail)

        domain_snap = (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "domain",
                models.ScorecardSnapshot.key == f"domain-{suffix}",
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .one()
        )
        team_snap = (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "team",
                models.ScorecardSnapshot.key == f"team-{suffix}",
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .one()
        )
        assert domain_snap.label == f"domain-{suffix}"
        assert team_snap.label == f"team-{suffix}"
        assert (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "domain",
                models.ScorecardSnapshot.key == f"schema_not_domain_{suffix}",
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .count()
            == 0
        )
        assert (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "team",
                models.ScorecardSnapshot.key == f"wrong-team-{suffix}",
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .count()
            == 0
        )

        for exc in open_exceptions:
            exc.status = "resolved"
        db.add(
            models.CheckRun(
                check_id=checks[2].id,
                dataset_id=dataset.id,
                status="pass",
                violation_count=0,
                metrics={},
                started_at=utcnow() + timedelta(seconds=1),
            )
        )
        db.commit()

        updated = scorecards.capture_scorecard_snapshots(db, day)
        db.commit()
        updated_live_score = next(
            s for s in scorecards.load_dataset_scores(db) if s.dataset_id == dataset.id
        )
        db.refresh(dataset_snap)

        assert len(updated) == len(snapshots)
        assert (
            db.query(models.ScorecardSnapshot)
            .filter(
                models.ScorecardSnapshot.grain == "dataset",
                models.ScorecardSnapshot.key == str(dataset.id),
                models.ScorecardSnapshot.snapshot_date == day,
            )
            .count()
            == 1
        )
        assert updated_live_score.score == 91.43
        assert updated_live_score.open_exceptions == 0
        assert updated_live_score.slo_status == "met"
        assert dataset_snap.score == updated_live_score.score
        assert dataset_snap.open_exception_count == 0
        assert dataset_snap.slo_status == "met"


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
