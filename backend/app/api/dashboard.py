from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import check_out, dataset_out, run_out
from app.config import get_settings
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user, visible_connection_ids, visible_dataset_ids

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=schemas.DashboardOut)
def summary(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    now = utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    # Connection-grant scoping (#159): every figure is restricted to the caller's
    # visible connections. None -> unrestricted (admin / zero-grant legacy).
    vis = visible_connection_ids(db, user)
    visible_ds = visible_dataset_ids(db, user)

    def by_conn(q):  # Dataset-rooted query -> filter on Dataset.connection_id
        return q.filter(models.Dataset.connection_id.in_(vis)) if vis is not None else q

    def by_ds(q, col):  # dataset_id-keyed query (Check / CheckRun / ExceptionRecord)
        return q.filter(col.in_(visible_ds)) if visible_ds is not None else q

    datasets = by_conn(db.query(models.Dataset)).count()
    active_checks = by_ds(
        db.query(models.Check).filter(models.Check.status == "active"), models.Check.dataset_id
    ).count()
    proposed_checks = by_ds(
        db.query(models.Check).filter(models.Check.status == "proposed"), models.Check.dataset_id
    ).count()
    runs_24h = by_ds(
        db.query(models.CheckRun).filter(models.CheckRun.started_at >= day_ago),
        models.CheckRun.dataset_id,
    ).count()
    failing_checks = by_ds(
        db.query(models.Check).filter(
            models.Check.status == "active", models.Check.last_status.in_(["fail", "error"])
        ),
        models.Check.dataset_id,
    ).count()
    open_exceptions = by_ds(
        db.query(models.ExceptionRecord).filter(models.ExceptionRecord.status == "open"),
        models.ExceptionRecord.dataset_id,
    ).count()

    week_runs = (
        by_ds(
            db.query(models.CheckRun.status, func.count()).filter(
                models.CheckRun.started_at >= week_ago
            ),
            models.CheckRun.dataset_id,
        )
        .group_by(models.CheckRun.status)
        .all()
    )
    week_counts = dict(week_runs)
    week_total = sum(week_counts.values())
    pass_rate = round(week_counts.get("pass", 0) / week_total, 4) if week_total else None

    # daily trend over the last 14 days
    rows = (
        by_ds(
            db.query(models.CheckRun).filter(models.CheckRun.started_at >= two_weeks_ago),
            models.CheckRun.dataset_id,
        )
        .order_by(models.CheckRun.started_at)
        .all()
    )
    by_day: dict[str, dict[str, int]] = {}
    for i in range(13, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        by_day[day] = {"pass": 0, "warn": 0, "fail": 0, "error": 0}
    for r in rows:
        day = r.started_at.strftime("%Y-%m-%d")
        if day in by_day and r.status in by_day[day]:
            by_day[day][r.status] += 1
    trend = [
        schemas.TrendPoint(
            day=d, passed=v["pass"], warned=v["warn"], failed=v["fail"], errored=v["error"]
        )
        for d, v in by_day.items()
    ]

    recent = (
        by_ds(db.query(models.CheckRun), models.CheckRun.dataset_id)
        .order_by(models.CheckRun.id.desc())
        .limit(10)
        .all()
    )

    worst = (
        by_conn(
            db.query(models.Dataset)
            .join(models.ExceptionRecord, models.ExceptionRecord.dataset_id == models.Dataset.id)
            .filter(models.ExceptionRecord.status == "open")
        )
        .group_by(models.Dataset.id)
        .order_by(func.count(models.ExceptionRecord.id).desc())
        .limit(5)
        .all()
    )

    return schemas.DashboardOut(
        datasets=datasets,
        active_checks=active_checks,
        proposed_checks=proposed_checks,
        runs_24h=runs_24h,
        failing_checks=failing_checks,
        open_exceptions=open_exceptions,
        llm_enabled=get_settings().llm_enabled,
        pass_rate_7d=pass_rate,
        trend=trend,
        recent_runs=[run_out(db, r) for r in recent],
        worst_datasets=[dataset_out(db, d) for d in worst],
    )


def _mover_name(ds: models.Dataset | None, dataset_id: int) -> str:
    if ds is None:
        return f"dataset {dataset_id}"
    return ds.display_name or (f"{ds.schema_name}.{ds.table_name}" if ds.schema_name else ds.table_name)


@router.get("/console", response_model=schemas.DashboardConsoleOut)
def console(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """The analyst's 9am work queue (#64): what's mine, what's new, what regressed,
    what's failing now, and which datasets moved most — a work queue, not a status
    brochure. Every "24h" figure is a rolling window from utcnow(), not a calendar
    day, and the UI labels it "last 24h" so non-UTC analysts can reconcile it."""
    now = utcnow()
    day_ago = now - timedelta(hours=24)
    exc = models.ExceptionRecord

    # Connection-grant scoping (#159): None -> unrestricted (admin / zero-grant).
    visible_ds = visible_dataset_ids(db, user)

    def by_ds(q, col=exc.dataset_id):
        return q.filter(col.in_(visible_ds)) if visible_ds is not None else q

    new_exceptions_24h = by_ds(db.query(exc).filter(exc.first_seen_at >= day_ago)).count()
    resolved_24h = by_ds(
        db.query(exc).filter(exc.status == "resolved", exc.marked_at >= day_ago)
    ).count()
    # Approximation until system events are queryable (#56): an open exception that
    # has been triaged (marked_at set) yet recurred (occurrence_count > 1) is a
    # regression. Replace with an event-kind count once events land.
    regressed_open = by_ds(
        db.query(exc).filter(
            exc.status == "open", exc.occurrence_count > 1, exc.marked_at.isnot(None)
        )
    ).count()
    assigned_to_me_open = by_ds(
        db.query(exc).filter(exc.status == "open", exc.assigned_to_id == user.id)
    ).count()
    open_total = by_ds(db.query(exc).filter(exc.status == "open")).count()

    # Failing right now: active checks whose last run failed/errored, error first.
    severity_order = case(
        (models.Check.severity == "error", 0), (models.Check.severity == "warn", 1), else_=2
    )
    failing = (
        by_ds(
            db.query(models.Check).filter(
                models.Check.status == "active",
                models.Check.last_status.in_(["fail", "error"]),
            ),
            models.Check.dataset_id,
        )
        .order_by(severity_order, models.Check.last_run_at.desc())
        .limit(8)
        .all()
    )
    failing_now = [check_out(c) for c in failing]

    # Biggest movers: datasets with the most new exceptions in the last 24h. Two
    # grouped aggregates merged in Python, joined to Dataset for names — no N+1.
    opened_rows = (
        by_ds(db.query(exc.dataset_id, func.count()).filter(exc.first_seen_at >= day_ago))
        .group_by(exc.dataset_id)
        .all()
    )
    opened = dict(opened_rows)
    top_ids = [d for d, _ in sorted(opened_rows, key=lambda r: r[1], reverse=True)[:5]]
    resolved_by: dict[int, int] = {}
    open_by: dict[int, int] = {}
    ds_by_id: dict[int, models.Dataset] = {}
    if top_ids:
        resolved_by = dict(
            db.query(exc.dataset_id, func.count())
            .filter(
                exc.dataset_id.in_(top_ids),
                exc.status == "resolved",
                exc.marked_at >= day_ago,
            )
            .group_by(exc.dataset_id)
            .all()
        )
        open_by = dict(
            db.query(exc.dataset_id, func.count())
            .filter(exc.dataset_id.in_(top_ids), exc.status == "open")
            .group_by(exc.dataset_id)
            .all()
        )
        ds_by_id = {
            d.id: d
            for d in db.query(models.Dataset).filter(models.Dataset.id.in_(top_ids)).all()
        }
    movers = [
        schemas.DatasetMover(
            dataset_id=i,
            dataset_name=_mover_name(ds_by_id.get(i), i),
            opened_24h=opened.get(i, 0),
            resolved_24h=resolved_by.get(i, 0),
            open_total=open_by.get(i, 0),
        )
        for i in top_ids
    ]

    return schemas.DashboardConsoleOut(
        new_exceptions_24h=new_exceptions_24h,
        resolved_24h=resolved_24h,
        regressed_open=regressed_open,
        assigned_to_me_open=assigned_to_me_open,
        open_total=open_total,
        failing_now=failing_now,
        movers=movers,
    )
