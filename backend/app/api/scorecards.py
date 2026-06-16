"""Scorecard read APIs over app metadata only (issue #118)."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app import models, schemas
from app.core import scorecards
from app.db import get_db
from app.security import get_current_user

router = APIRouter(prefix="/scorecards", tags=["scorecards"])


def _latest_statuses(db: Session, check_ids: list[int]) -> dict[int, str | None]:
    if not check_ids:
        return {}
    rows = (
        db.query(models.CheckRun)
        .filter(models.CheckRun.check_id.in_(check_ids))
        .order_by(models.CheckRun.check_id, models.CheckRun.started_at.desc(), models.CheckRun.id.desc())
        .all()
    )
    latest: dict[int, str | None] = {}
    for row in rows:
        latest.setdefault(row.check_id, row.status)
    return latest


def _active_exception_counts(db: Session) -> dict[int, int]:
    rows = (
        db.query(models.ExceptionRecord.dataset_id, func.count(models.ExceptionRecord.id))
        .filter(models.ExceptionRecord.status.in_(sorted(scorecards.ACTIVE_EXCEPTION_STATUSES)))
        .group_by(models.ExceptionRecord.dataset_id)
        .all()
    )
    return {int(dataset_id): int(count) for dataset_id, count in rows}


def _scores(db: Session) -> list[scorecards.DatasetScore]:
    datasets = (
        db.query(models.Dataset)
        .options(selectinload(models.Dataset.knowledge), selectinload(models.Dataset.checks))
        .order_by(models.Dataset.id)
        .all()
    )
    active_check_ids = [
        int(check.id) for ds in datasets for check in ds.checks if check.status == "active"
    ]
    latest = _latest_statuses(db, active_check_ids)
    exception_counts = _active_exception_counts(db)
    return [
        scorecards.score_dataset(
            ds,
            ds.knowledge,
            ds.checks,
            latest,
            exception_counts.get(ds.id, 0),
        )
        for ds in datasets
    ]


def _dataset_out(row: scorecards.DatasetScore) -> schemas.ScorecardDatasetOut:
    return schemas.ScorecardDatasetOut(**asdict(row))


def _rollup_out(row: scorecards.RollupScore) -> schemas.ScorecardRollupOut:
    return schemas.ScorecardRollupOut(**asdict(row))


def _summary_out(
    global_score: scorecards.RollupScore,
    worst_rollups: list[scorecards.RollupScore],
    top_failing: list[scorecards.DatasetScore],
) -> schemas.ScorecardSummaryOut:
    return schemas.ScorecardSummaryOut(
        score=global_score.score,
        slo_target=global_score.slo_target,
        slo_status=global_score.slo_status,
        score_gap=global_score.score_gap,
        total_datasets=global_score.total_datasets,
        scored_datasets=global_score.scored_datasets,
        unknown_datasets=global_score.unknown_datasets,
        active_checks=global_score.active_checks,
        passing_checks=global_score.passing_checks,
        warning_checks=global_score.warning_checks,
        failing_checks=global_score.failing_checks,
        error_checks=global_score.error_checks,
        unknown_checks=global_score.unknown_checks,
        open_exceptions=global_score.open_exceptions,
        slo_met=global_score.slo_met,
        slo_at_risk=global_score.slo_at_risk,
        slo_breached=global_score.slo_breached,
        slo_unknown=global_score.slo_unknown,
        slo_disabled=global_score.slo_disabled,
        worst_rollups=[_rollup_out(r) for r in worst_rollups],
        top_failing_datasets=[_dataset_out(r) for r in top_failing],
    )


def _matches_filter(value: str, expected: str | None) -> bool:
    if expected is None:
        return True
    return value.strip().lower() == expected.strip().lower()


@router.get("/summary", response_model=schemas.ScorecardSummaryOut)
def summary(db: Session = Depends(get_db), _: models.User = Depends(get_current_user)):
    dataset_scores = _scores(db)
    global_score = scorecards.aggregate_scores(dataset_scores)

    rollups: list[scorecards.RollupScore] = []
    for dimension in ("domain", "team"):
        rollups.extend(scorecards.rollup_scores(dataset_scores, dimension))
    worst_rollups = sorted(
        [r for r in rollups if r.score is not None],
        key=lambda r: (
            r.slo_status != "breached",
            r.score if r.score is not None else 101.0,
            r.label.lower(),
        ),
    )[:5]
    top_failing = [r for r in scorecards.sort_datasets_for_attention(dataset_scores) if r.score is not None][:10]
    return _summary_out(global_score, worst_rollups, top_failing)


@router.get("/rollups", response_model=list[schemas.ScorecardRollupOut])
def rollups(
    dimension: schemas.ScorecardDimension = Query("domain"),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    return [_rollup_out(r) for r in scorecards.rollup_scores(_scores(db), dimension)]


@router.get("/datasets", response_model=schemas.ScorecardDatasetPageOut)
def datasets(
    domain: str | None = None,
    team: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    rows = [
        row
        for row in _scores(db)
        if _matches_filter(row.domain, domain) and _matches_filter(row.team, team)
    ]
    rows = scorecards.sort_datasets_for_attention(rows)
    total = len(rows)
    page = rows[offset : offset + limit]
    return schemas.ScorecardDatasetPageOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[_dataset_out(row) for row in page],
    )
