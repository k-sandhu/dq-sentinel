from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core import scorecards
from app.db import session_factory
from app.models import Check, CheckRun, Connection, Dataset, ExceptionRecord, TableKnowledge


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
        db.add(TableKnowledge(dataset_id=dataset.id, importance="critical", owner="Data Quality"))
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
        for status in ("open", "expected", "resolved", "muted"):
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
    assert item["score"] == 98
    assert item["slo_status"] == "met"
    assert "score_drivers" in item
    assert "dsn" not in item

    rollups = client.get("/api/v1/scorecards/rollups?dimension=domain", headers=admin_headers)
    assert rollups.status_code == 200
    assert any(row["key"] == "" and row["label"] == "Unassigned" for row in rollups.json())


@pytest.mark.parametrize("dimension", ["domain", "team", "owner", "importance"])
def test_scorecard_rollup_dimensions(client, admin_headers, dimension):
    resp = client.get(f"/api/v1/scorecards/rollups?dimension={dimension}", headers=admin_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
