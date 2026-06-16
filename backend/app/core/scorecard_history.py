"""Metadata-only scorecard snapshot capture (#119).

Companion to :mod:`app.core.scorecards` (the live read API): current quality
points are derived only from app metadata tables and persisted as daily
aggregates for trend charts. Grouping dimensions (domain/team/owner/importance)
reuse the governance fields on ``TableKnowledge`` so the persisted history lines
up with the live rollups served by the scorecards API.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app import models
from app.core import scorecards
from app.models import utcnow

SCORECARD_GRAINS = ("global", "domain", "team", "owner", "importance", "dataset")


@dataclass(slots=True)
class DatasetSnapshotScore:
    dataset: models.Dataset
    scorecard: scorecards.DatasetScore


def _snapshot_day(snapshot_date: date | None) -> date:
    return snapshot_date or utcnow().date()


def _label_for_dataset(dataset: models.Dataset) -> str:
    if dataset.display_name:
        return dataset.display_name
    if dataset.schema_name:
        return f"{dataset.schema_name}.{dataset.table_name}"
    return dataset.table_name


def _snapshot_key(value: str) -> str:
    return value[:255]


def _open_exception_counts(db: Session) -> dict[int, int]:
    rows = (
        db.query(models.ExceptionRecord.dataset_id, func.count(models.ExceptionRecord.id))
        .filter(models.ExceptionRecord.status == "open")
        .group_by(models.ExceptionRecord.dataset_id)
        .all()
    )
    return {int(dataset_id): int(count) for dataset_id, count in rows}


def _latest_statuses(db: Session, check_ids: list[int]) -> dict[int, str | None]:
    if not check_ids:
        return {}
    rows = (
        db.query(
            models.CheckRun.check_id,
            models.CheckRun.status,
            models.CheckRun.started_at,
            models.CheckRun.id,
        )
        .filter(models.CheckRun.check_id.in_(check_ids))
        .order_by(
            models.CheckRun.check_id,
            models.CheckRun.started_at.desc(),
            models.CheckRun.id.desc(),
        )
        .all()
    )
    statuses: dict[int, str | None] = {}
    for check_id, status, _started_at, _run_id in rows:
        statuses.setdefault(int(check_id), status)
    return statuses


def _latest_statuses_as_of(
    db: Session, check_ids: list[int], as_of: datetime
) -> dict[int, str | None]:
    if not check_ids:
        return {}
    rows = (
        db.query(
            models.CheckRun.check_id,
            models.CheckRun.status,
            models.CheckRun.started_at,
            models.CheckRun.id,
        )
        .filter(models.CheckRun.check_id.in_(check_ids), models.CheckRun.started_at <= as_of)
        .order_by(
            models.CheckRun.check_id,
            models.CheckRun.started_at.desc(),
            models.CheckRun.id.desc(),
        )
        .all()
    )
    statuses: dict[int, str | None] = {}
    for check_id, status, _started_at, _run_id in rows:
        statuses.setdefault(int(check_id), status)
    return statuses


def _status_map(db: Session, check_ids: list[int], as_of: datetime | None) -> dict[int, str | None]:
    if as_of is None:
        return _latest_statuses(db, check_ids)

    historical = _latest_statuses_as_of(db, check_ids, as_of)
    # `score_dataset` falls back to Check.last_status when a check id is absent.
    # Historical snapshots must not use current last_status for checks that had
    # no run yet, so every active check gets an explicit status bucket.
    return {check_id: historical.get(check_id) or "unknown" for check_id in check_ids}


def _dataset_scores(
    db: Session,
    *,
    as_of: datetime | None = None,
    include_open_exceptions: bool = True,
) -> list[DatasetSnapshotScore]:
    datasets = (
        db.query(models.Dataset)
        .options(selectinload(models.Dataset.knowledge), selectinload(models.Dataset.checks))
        .order_by(models.Dataset.id)
        .all()
    )
    active_check_ids = [
        int(check.id) for dataset in datasets for check in dataset.checks if check.status == "active"
    ]
    latest_statuses = _status_map(db, active_check_ids, as_of)
    open_counts = _open_exception_counts(db) if include_open_exceptions else {}

    scores: list[DatasetSnapshotScore] = []
    for dataset in datasets:
        scored = scorecards.score_dataset(
            dataset,
            dataset.knowledge,
            dataset.checks,
            latest_statuses,
            int(open_counts.get(int(dataset.id), 0)),
        )
        scores.append(DatasetSnapshotScore(dataset=dataset, scorecard=scored))
    return scores


def _aggregate_payload(
    *,
    grain: str,
    key: str,
    label: str,
    snapshot_date: date,
    datasets: list[DatasetSnapshotScore],
    include_open_exceptions: bool,
    as_of: datetime | None,
) -> dict[str, Any]:
    score_rows = [dataset.scorecard for dataset in datasets]
    rollup = scorecards.aggregate_scores(score_rows, dimension=grain, key=key, label=label)
    status_counts = {
        "pass": rollup.passing_checks,
        "warn": rollup.warning_checks,
        "fail": rollup.failing_checks,
        "error": rollup.error_checks,
        "unknown": rollup.unknown_checks,
    }
    detail: dict[str, Any] = {
        "schema_version": 1,
        "scoring_adapter": "live_scorecards_v1",
        "status_counts": status_counts,
        "sparse_history": True,
        "score_basis": "severity-weighted active-check status with open-exception penalty",
        "slo_basis": "TableKnowledge target when present, otherwise importance default",
        "slo_counts": {
            "met": rollup.slo_met,
            "at_risk": rollup.slo_at_risk,
            "breached": rollup.slo_breached,
            "unknown": rollup.slo_unknown,
            "disabled": rollup.slo_disabled,
        },
    }
    if as_of is not None:
        detail["as_of"] = as_of.isoformat()
        detail["score_basis"] = "latest check run status at or before as_of; missing runs score as unknown"
    if not include_open_exceptions:
        detail["exception_pressure"] = "omitted; historical open-exception state is not reconstructable"

    return {
        "grain": grain,
        "key": key,
        "label": label[:255],
        "snapshot_date": snapshot_date,
        "score": rollup.score,
        "slo_target": rollup.slo_target,
        "slo_status": rollup.slo_status,
        "dataset_count": rollup.total_datasets,
        "active_check_count": rollup.active_checks,
        "open_exception_count": rollup.open_exceptions,
        "breached_dataset_count": rollup.slo_breached,
        "detail": detail,
    }


def _snapshot_payloads(
    db: Session,
    *,
    snapshot_date: date,
    as_of: datetime | None = None,
    include_open_exceptions: bool = True,
) -> list[dict[str, Any]]:
    scores = _dataset_scores(db, as_of=as_of, include_open_exceptions=include_open_exceptions)
    payloads = [
        _aggregate_payload(
            grain="global",
            key="global",
            label="All datasets",
            snapshot_date=snapshot_date,
            datasets=scores,
            include_open_exceptions=include_open_exceptions,
            as_of=as_of,
        )
    ]

    def add_group(
        grain: str, key: str, label: str, grouped_scores: list[DatasetSnapshotScore]
    ) -> None:
        payloads.append(
            _aggregate_payload(
                grain=grain,
                key=_snapshot_key(key),
                label=label,
                snapshot_date=snapshot_date,
                datasets=grouped_scores,
                include_open_exceptions=include_open_exceptions,
                as_of=as_of,
            )
        )

    for dataset_score in scores:
        add_group(
            "dataset",
            str(dataset_score.dataset.id),
            _label_for_dataset(dataset_score.dataset),
            [dataset_score],
        )

    grouped: dict[tuple[str, str], list[DatasetSnapshotScore]] = defaultdict(list)
    labels: dict[tuple[str, str], str] = {}
    for dimension in ("domain", "team", "owner", "importance"):
        for dataset_score in scores:
            key, label = scorecards.rollup_key(dataset_score.scorecard, dimension)
            safe_key = key[:255]
            grouped[(dimension, safe_key)].append(dataset_score)
            labels[(dimension, safe_key)] = label

    for (grain, key), grouped_scores in sorted(grouped.items()):
        add_group(grain, key, labels[(grain, key)], grouped_scores)

    return payloads


def _upsert_snapshot(db: Session, payload: dict[str, Any]) -> models.ScorecardSnapshot:
    existing = (
        db.query(models.ScorecardSnapshot)
        .filter(
            models.ScorecardSnapshot.grain == payload["grain"],
            models.ScorecardSnapshot.key == payload["key"],
            models.ScorecardSnapshot.snapshot_date == payload["snapshot_date"],
        )
        .first()
    )
    if existing is None:
        snap = models.ScorecardSnapshot(**payload)
        db.add(snap)
        db.flush()
        return snap

    for field in (
        "label",
        "score",
        "slo_target",
        "slo_status",
        "dataset_count",
        "active_check_count",
        "open_exception_count",
        "breached_dataset_count",
        "detail",
    ):
        setattr(existing, field, payload[field])
    db.flush()
    return existing


def capture_scorecard_snapshots(
    db: Session,
    snapshot_date: date | None = None,
    *,
    as_of: datetime | None = None,
    include_open_exceptions: bool = True,
) -> list[models.ScorecardSnapshot]:
    """Idempotently capture daily scorecard snapshots.

    The caller owns the transaction. Running this more than once for the same
    ``(grain, key, snapshot_date)`` updates the aggregate values in place.
    """
    day = _snapshot_day(snapshot_date)
    payloads = _snapshot_payloads(
        db, snapshot_date=day, as_of=as_of, include_open_exceptions=include_open_exceptions
    )
    return [_upsert_snapshot(db, payload) for payload in payloads]


def backfill_scorecard_snapshots(
    db: Session,
    *,
    days: int = 90,
    through: date | None = None,
) -> list[models.ScorecardSnapshot]:
    """Backfill sparse daily snapshots from existing CheckRun history.

    Historical check status is reconstructed from the latest run at or before
    each day. Historical open-exception state is not reconstructable from current
    metadata, so backfilled points explicitly omit that pressure in ``detail``.
    The caller owns the transaction.
    """
    end = through or utcnow().date()
    start = end - timedelta(days=max(1, days) - 1)
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)
    run_days = {
        started_at.date()
        for (started_at,) in db.query(models.CheckRun.started_at)
        .filter(models.CheckRun.started_at >= start_dt, models.CheckRun.started_at <= end_dt)
        .all()
    }
    out: list[models.ScorecardSnapshot] = []
    for day in sorted(run_days):
        as_of = datetime.combine(day, time.max)
        out.extend(
            capture_scorecard_snapshots(
                db,
                day,
                as_of=as_of,
                include_open_exceptions=False,
            )
        )
    return out
