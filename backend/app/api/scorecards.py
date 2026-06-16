"""Scorecard read APIs over app metadata only (issues #118, #119)."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.core import scorecards
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user

router = APIRouter(prefix="/scorecards", tags=["scorecards"])


def _scores(db: Session) -> list[scorecards.DatasetScore]:
    return scorecards.load_dataset_scores(db)


def _dataset_out(row: scorecards.DatasetScore) -> schemas.ScorecardDatasetOut:
    return schemas.ScorecardDatasetOut(**scorecards.dataset_score_dict(row))


def _rollup_out(row: scorecards.RollupScore) -> schemas.ScorecardRollupOut:
    return schemas.ScorecardRollupOut(**scorecards.rollup_score_dict(row))


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


@router.get("/history", response_model=schemas.ScorecardHistoryOut)
def history(
    grain: schemas.ScorecardHistoryGrain = Query("global"),
    key: str | None = None,
    days: int = Query(90, ge=1, le=366),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    """Sparse daily scorecard points, ordered oldest first.

    Snapshot rows are aggregate app metadata only. Missing dates are omitted so
    clients can render honest gaps without expensive recomputation.
    """
    history_key = key.strip() if key and key.strip() else None
    if grain == "global" and history_key is None:
        history_key = "global"
    cutoff = utcnow().date() - timedelta(days=days - 1)

    query = db.query(models.ScorecardSnapshot).filter(
        models.ScorecardSnapshot.grain == grain,
        models.ScorecardSnapshot.snapshot_date >= cutoff,
    )
    if history_key is not None:
        query = query.filter(models.ScorecardSnapshot.key == history_key)
    points = query.order_by(
        models.ScorecardSnapshot.snapshot_date.asc(),
        models.ScorecardSnapshot.key.asc(),
    ).all()

    return schemas.ScorecardHistoryOut(
        grain=grain,
        key=history_key,
        days=days,
        sparse=True,
        points=points,
    )
