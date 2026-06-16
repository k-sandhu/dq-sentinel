"""SLA evaluation (issue #102).

Computes attainment / error-budget / MTTR for an SLA from CheckRun and
ExceptionRecord history, writes an SLAEvaluation rollup, and (for new breaches)
emits a metric + notification. Invoked periodically by the worker
(core/scheduler.maybe_evaluate_slas) and on demand via the API.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import notify
from app.models import Check, CheckRun, ExceptionRecord, SLADefinition, SLAEvaluation, utcnow
from app.observability import SLA_ATTAINMENT, SLA_BREACHES

log = logging.getLogger(__name__)

WINDOWS: dict[str, timedelta] = {"rolling_7d": timedelta(days=7), "rolling_30d": timedelta(days=30)}
GOOD_STATUSES = ("pass", "warn")
BAD_STATUSES = ("fail", "error")
TARGET_TYPES = ("freshness", "volume", "check_success")
SCOPES = ("dataset", "check")


def target_check_ids(db: Session, sla: SLADefinition) -> list[int]:
    """Which checks count toward this SLA."""
    if sla.scope == "check":
        return [sla.scope_id]
    q = db.query(Check.id).filter(Check.dataset_id == sla.scope_id)
    if sla.target_type == "freshness":
        q = q.filter(Check.check_type == "freshness")
    elif sla.target_type == "volume":
        q = q.filter(Check.check_type.in_(["row_count_min", "row_count_anomaly"]))
    # check_success -> every check on the dataset
    return [row[0] for row in q.all()]


def _mttr_seconds(db: Session, check_ids: list[int], start: datetime) -> int | None:
    """Mean time-to-resolve for exceptions resolved within the window."""
    if not check_ids:
        return None
    recs = (
        db.query(ExceptionRecord.first_seen_at, ExceptionRecord.marked_at)
        .filter(
            ExceptionRecord.check_id.in_(check_ids),
            ExceptionRecord.status == "resolved",
            ExceptionRecord.marked_at.isnot(None),
            ExceptionRecord.marked_at >= start,
        )
        .all()
    )
    durations = [
        (marked - first).total_seconds()
        for first, marked in recs
        if first and marked and marked >= first
    ]
    if not durations:
        return None
    return int(sum(durations) / len(durations))


def evaluate_sla(db: Session, sla: SLADefinition, now: datetime | None = None) -> SLAEvaluation:
    """Compute and persist one SLAEvaluation rollup (does not commit)."""
    now = now or utcnow()
    start = now - WINDOWS.get(sla.window, WINDOWS["rolling_30d"])
    check_ids = target_check_ids(db, sla)

    good = bad = 0
    if check_ids:
        rows = (
            db.query(CheckRun.status, func.count())
            .filter(CheckRun.check_id.in_(check_ids), CheckRun.started_at >= start)
            .group_by(CheckRun.status)
            .all()
        )
        counts = {status: int(n) for status, n in rows}
        good = sum(counts.get(s, 0) for s in GOOD_STATUSES)
        bad = sum(counts.get(s, 0) for s in BAD_STATUSES)

    total = good + bad
    attainment = (good / total) if total else 1.0  # no runs yet => vacuously met
    allowed = max(0.0, 1.0 - sla.objective)  # error budget
    if total == 0:
        budget = 0.0
    elif allowed <= 0:  # objective == 1.0: any bad run blows the whole budget
        budget = 1.0 if bad else 0.0
    else:
        budget = min(2.0, (bad / total) / allowed)
    breached = attainment < sla.objective

    ev = SLAEvaluation(
        sla_id=sla.id,
        evaluated_at=now,
        window_start=start,
        window_end=now,
        attainment=round(attainment, 6),
        budget_consumed=round(budget, 6),
        good=good,
        bad=bad,
        breached=breached,
        mttr_seconds=_mttr_seconds(db, check_ids, start),
    )
    db.add(ev)
    db.flush()
    return ev


def latest_evaluation(db: Session, sla_id: int) -> SLAEvaluation | None:
    return (
        db.query(SLAEvaluation)
        .filter(SLAEvaluation.sla_id == sla_id)
        .order_by(SLAEvaluation.id.desc())
        .first()
    )


def evaluate_all(db: Session, now: datetime | None = None) -> list[SLAEvaluation]:
    """Evaluate every enabled SLA, emit metrics, and notify on NEW breaches."""
    now = now or utcnow()
    out: list[SLAEvaluation] = []
    for sla in db.query(SLADefinition).filter(SLADefinition.enabled.is_(True)).all():
        prev = latest_evaluation(db, sla.id)  # read BEFORE inserting the new rollup
        ev = evaluate_sla(db, sla, now)
        SLA_ATTAINMENT.labels(str(sla.id)).set(ev.attainment)
        if ev.breached and not (prev and prev.breached):  # transition into breach
            SLA_BREACHES.labels(str(sla.id)).inc()
            try:
                notify.dispatch_sla_breach(db, sla, ev)
            except Exception:  # noqa: BLE001 - a dead channel must never break evaluation
                log.warning("SLA breach notification failed for sla %s", sla.id, exc_info=True)
        out.append(ev)
    db.commit()
    return out


def ensure_freshness_sla(db: Session, dataset_id: int, created_by_id: int | None) -> SLADefinition | None:
    """Create a default dataset freshness SLA if none exists yet (#102).

    Called when a dataset's ``freshness_sla_hours`` knowledge is set, turning that
    previously-inert metadata into a tracked, enforced SLA. Returns the new SLA or
    None if one already covers the dataset's freshness.
    """
    existing = (
        db.query(SLADefinition)
        .filter(
            SLADefinition.scope == "dataset",
            SLADefinition.scope_id == dataset_id,
            SLADefinition.target_type == "freshness",
        )
        .first()
    )
    if existing is not None:
        return None
    sla = SLADefinition(
        name="Freshness SLA",
        scope="dataset",
        scope_id=dataset_id,
        target_type="freshness",
        objective=0.99,
        window="rolling_30d",
        created_by_id=created_by_id,
    )
    db.add(sla)
    db.flush()
    return sla
