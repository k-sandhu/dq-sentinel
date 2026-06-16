"""Metadata-only scorecard snapshot capture (#119).

The public scorecard API from #118 is not present on main yet. This module is a
narrow internal adapter around the same intended contract: current quality
points are derived only from app metadata tables and persisted as daily
aggregates for trend charts.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.models import utcnow

SCORECARD_GRAINS = ("global", "domain", "team", "owner", "importance", "dataset")
GOOD_STATUSES = ("pass", "warn")
BAD_STATUSES = ("fail", "error")
KNOWN_STATUSES = GOOD_STATUSES + BAD_STATUSES
TEAM_METADATA_KEYS = ("team", "data_team", "owner_team", "steward_team")


@dataclass(slots=True)
class DatasetScore:
    dataset: models.Dataset
    label: str
    score: float | None
    slo_target: float | None
    slo_status: str
    active_check_count: int
    open_exception_count: int
    breached: bool
    status_counts: dict[str, int]


def _snapshot_day(snapshot_date: date | None) -> date:
    return snapshot_date or utcnow().date()


def _label_for_dataset(dataset: models.Dataset) -> str:
    if dataset.display_name:
        return dataset.display_name
    if dataset.schema_name:
        return f"{dataset.schema_name}.{dataset.table_name}"
    return dataset.table_name


def _clean_key(value: str | None, fallback: str) -> str:
    key = (value or "").strip()
    return (key or fallback)[:255]


def _team_for(dataset: models.Dataset) -> str | None:
    exploration = dataset.exploration if isinstance(dataset.exploration, dict) else {}
    for key in TEAM_METADATA_KEYS:
        value = exploration.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _score_from_counts(status_counts: dict[str, int]) -> float | None:
    good = sum(status_counts.get(s, 0) for s in GOOD_STATUSES)
    bad = sum(status_counts.get(s, 0) for s in BAD_STATUSES)
    evaluated = good + bad
    if evaluated == 0:
        return None
    return round((good / evaluated) * 100.0, 2)


def _slo_status(score: float | None, target: float | None, breached: bool) -> str:
    if breached:
        return "breached"
    if score is None or target is None:
        return "unknown"
    return "met" if score >= target else "breached"


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _open_exception_counts(db: Session) -> dict[int, int]:
    rows = (
        db.query(models.ExceptionRecord.dataset_id, func.count(models.ExceptionRecord.id))
        .filter(models.ExceptionRecord.status == "open")
        .group_by(models.ExceptionRecord.dataset_id)
        .all()
    )
    return {int(dataset_id): int(count) for dataset_id, count in rows}


def _sla_targets_by_dataset(db: Session) -> dict[int, float]:
    check_dataset = {
        int(check_id): int(dataset_id)
        for check_id, dataset_id in db.query(models.Check.id, models.Check.dataset_id).all()
    }
    raw_targets: dict[int, list[float]] = defaultdict(list)
    slas = db.query(models.SLADefinition).filter(models.SLADefinition.enabled.is_(True)).all()
    for sla in slas:
        target = round(sla.objective * 100.0, 2)
        if sla.scope == "dataset":
            raw_targets[int(sla.scope_id)].append(target)
        elif sla.scope == "check":
            dataset_id = check_dataset.get(int(sla.scope_id))
            if dataset_id is not None:
                raw_targets[dataset_id].append(target)
    return {
        dataset_id: round(sum(targets) / len(targets), 2)
        for dataset_id, targets in raw_targets.items()
        if targets
    }


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


def _dataset_scores(
    db: Session,
    *,
    as_of: datetime | None = None,
    include_open_exceptions: bool = True,
) -> list[DatasetScore]:
    datasets = db.query(models.Dataset).order_by(models.Dataset.id).all()
    active_checks = db.query(models.Check).filter(models.Check.status == "active").all()
    checks_by_dataset: dict[int, list[models.Check]] = defaultdict(list)
    for check in active_checks:
        checks_by_dataset[int(check.dataset_id)].append(check)

    historical_statuses = (
        _latest_statuses_as_of(db, [int(c.id) for c in active_checks], as_of) if as_of else {}
    )
    open_counts = _open_exception_counts(db) if include_open_exceptions else {}
    targets = _sla_targets_by_dataset(db)

    scores: list[DatasetScore] = []
    for dataset in datasets:
        status_counts = {"pass": 0, "warn": 0, "fail": 0, "error": 0, "unknown": 0}
        dataset_checks = checks_by_dataset.get(int(dataset.id), [])
        for check in dataset_checks:
            status = historical_statuses.get(int(check.id)) if as_of else check.last_status
            bucket = status if status in KNOWN_STATUSES else "unknown"
            status_counts[bucket] += 1

        score = _score_from_counts(status_counts)
        open_count = int(open_counts.get(int(dataset.id), 0))
        target = targets.get(int(dataset.id))
        breached = (
            open_count > 0
            or status_counts["fail"] > 0
            or status_counts["error"] > 0
            or (score is not None and target is not None and score < target)
        )
        scores.append(
            DatasetScore(
                dataset=dataset,
                label=_label_for_dataset(dataset),
                score=score,
                slo_target=target,
                slo_status=_slo_status(score, target, breached),
                active_check_count=len(dataset_checks),
                open_exception_count=open_count,
                breached=breached,
                status_counts=status_counts,
            )
        )
    return scores


def _aggregate_payload(
    *,
    grain: str,
    key: str,
    label: str,
    snapshot_date: date,
    datasets: list[DatasetScore],
    include_open_exceptions: bool,
    as_of: datetime | None,
) -> dict[str, Any]:
    status_counts = {"pass": 0, "warn": 0, "fail": 0, "error": 0, "unknown": 0}
    for dataset in datasets:
        for status, count in dataset.status_counts.items():
            status_counts[status] += count

    score = _score_from_counts(status_counts)
    targets = [dataset.slo_target for dataset in datasets if dataset.slo_target is not None]
    target = _average(targets)
    breached = any(dataset.breached for dataset in datasets)
    detail: dict[str, Any] = {
        "schema_version": 1,
        "scoring_adapter": "metadata_current_v1",
        "status_counts": status_counts,
        "sparse_history": True,
        "score_basis": "active check last_status pass/warn over evaluated active checks",
        "breach_basis": "open exceptions, fail/error checks, or score below SLO target",
    }
    if as_of is not None:
        detail["as_of"] = as_of.isoformat()
        detail["score_basis"] = "latest check run status at or before as_of"
    if not include_open_exceptions:
        detail["exception_pressure"] = "omitted; historical open-exception state is not reconstructable"

    return {
        "grain": grain,
        "key": key,
        "label": label[:255],
        "snapshot_date": snapshot_date,
        "score": score,
        "slo_target": target,
        "slo_status": _slo_status(score, target, breached),
        "dataset_count": len(datasets),
        "active_check_count": sum(dataset.active_check_count for dataset in datasets),
        "open_exception_count": sum(dataset.open_exception_count for dataset in datasets),
        "breached_dataset_count": sum(1 for dataset in datasets if dataset.breached),
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

    def add_group(grain: str, key: str, label: str, grouped_scores: list[DatasetScore]) -> None:
        payloads.append(
            _aggregate_payload(
                grain=grain,
                key=_clean_key(key, "unassigned"),
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
            dataset_score.label,
            [dataset_score],
        )

    grouped: dict[tuple[str, str], list[DatasetScore]] = defaultdict(list)
    labels: dict[tuple[str, str], str] = {}
    for dataset_score in scores:
        dataset = dataset_score.dataset
        domain = _clean_key(dataset.schema_name, "default")
        grouped[("domain", domain)].append(dataset_score)
        labels[("domain", domain)] = "Default schema" if domain == "default" else domain

        owner = _clean_key(dataset.knowledge.owner if dataset.knowledge else None, "unassigned")
        grouped[("owner", owner)].append(dataset_score)
        labels[("owner", owner)] = "Unassigned owner" if owner == "unassigned" else owner

        importance = _clean_key(dataset.knowledge.importance if dataset.knowledge else None, "unset")
        grouped[("importance", importance)].append(dataset_score)
        labels[("importance", importance)] = (
            "Unset importance" if importance == "unset" else importance
        )

        team = _team_for(dataset)
        if team:
            team_key = _clean_key(team, "unassigned")
            grouped[("team", team_key)].append(dataset_score)
            labels[("team", team_key)] = team_key

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
