from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.api.serialize import exception_out, run_out, warm_exception_refs
from app.db import get_db
from app.models import utcnow
from app.security import assert_dataset_visible, get_current_user, visible_dataset_ids

router = APIRouter(prefix="/runs", tags=["runs"])

SINCE_WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "14d": timedelta(days=14),
}


def _day_bounds(day: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.strptime(day, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(422, "day must be YYYY-MM-DD") from exc
    return start, start + timedelta(days=1)


@router.get("", response_model=list[schemas.RunOut])
def list_runs(
    response: Response,
    run_id: int | None = None,
    dataset_id: int | None = None,
    check_id: int | None = None,
    status: str | None = None,
    day: str | None = None,
    since: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    q = db.query(models.CheckRun)
    visible_ds = visible_dataset_ids(db, user)
    if visible_ds is not None:  # restrict to runs on granted connections (#159)
        q = q.filter(models.CheckRun.dataset_id.in_(visible_ds))
    if run_id is not None:
        q = q.filter(models.CheckRun.id == run_id)
    if dataset_id is not None:
        q = q.filter(models.CheckRun.dataset_id == dataset_id)
    if check_id is not None:
        q = q.filter(models.CheckRun.check_id == check_id)
    if status:
        q = q.filter(models.CheckRun.status == status)
    if day:
        start, end = _day_bounds(day)
        q = q.filter(models.CheckRun.started_at >= start, models.CheckRun.started_at < end)
    if since:
        delta = SINCE_WINDOWS.get(since)
        if delta is None:
            raise HTTPException(422, "since must be one of: 24h, 7d, 14d")
        q = q.filter(models.CheckRun.started_at >= utcnow() - delta)
    response.headers["X-Total-Count"] = str(q.count())
    runs = (
        q.options(joinedload(models.CheckRun.check).joinedload(models.Check.dataset))
        .order_by(models.CheckRun.id.desc())
        .offset(offset)
        .limit(min(limit, 200))
        .all()
    )
    # One GROUP BY replaces a COUNT per row (perf: a 100-run page issued 100
    # counts plus lazy check/dataset loads before this).
    counts: dict[int, int] = dict(
        db.query(models.ExceptionRecord.run_id, func.count(models.ExceptionRecord.id))
        .filter(models.ExceptionRecord.run_id.in_([r.id for r in runs]))
        .group_by(models.ExceptionRecord.run_id)
        .all()
    ) if runs else {}
    return [run_out(db, r, exception_count=counts.get(r.id, 0)) for r in runs]


@router.get("/{run_id}", response_model=schemas.RunOut)
def get_run(run_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    run = db.get(models.CheckRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    assert_dataset_visible(db, user, run.dataset_id)  # run on a visible connection (#159)
    return run_out(db, run)


@router.get("/{run_id}/exceptions", response_model=list[schemas.ExceptionOut])
def run_exceptions(
    run_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    run = db.get(models.CheckRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    assert_dataset_visible(db, user, run.dataset_id)  # run on a visible connection (#159)
    excs = (
        db.query(models.ExceptionRecord)
        .filter(models.ExceptionRecord.run_id == run_id)
        .order_by(models.ExceptionRecord.id)
        .all()
    )
    warm_exception_refs(db, excs)
    return [exception_out(db, e) for e in excs]
