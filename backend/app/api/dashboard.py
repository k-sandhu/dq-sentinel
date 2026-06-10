from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import dataset_out, run_out
from app.config import get_settings
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=schemas.DashboardOut)
def summary(db: Session = Depends(get_db), _: models.User = Depends(get_current_user)):
    now = utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    datasets = db.query(models.Dataset).count()
    active_checks = db.query(models.Check).filter(models.Check.status == "active").count()
    proposed_checks = db.query(models.Check).filter(models.Check.status == "proposed").count()
    runs_24h = db.query(models.CheckRun).filter(models.CheckRun.started_at >= day_ago).count()
    failing_checks = (
        db.query(models.Check)
        .filter(models.Check.status == "active", models.Check.last_status.in_(["fail", "error"]))
        .count()
    )
    open_exceptions = (
        db.query(models.ExceptionRecord).filter(models.ExceptionRecord.status == "open").count()
    )

    week_runs = (
        db.query(models.CheckRun.status, func.count())
        .filter(models.CheckRun.started_at >= week_ago)
        .group_by(models.CheckRun.status)
        .all()
    )
    week_counts = dict(week_runs)
    week_total = sum(week_counts.values())
    pass_rate = round(week_counts.get("pass", 0) / week_total, 4) if week_total else None

    # daily trend over the last 14 days
    rows = (
        db.query(models.CheckRun)
        .filter(models.CheckRun.started_at >= two_weeks_ago)
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
        db.query(models.CheckRun).order_by(models.CheckRun.id.desc()).limit(10).all()
    )

    worst = (
        db.query(models.Dataset)
        .join(models.ExceptionRecord, models.ExceptionRecord.dataset_id == models.Dataset.id)
        .filter(models.ExceptionRecord.status == "open")
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
