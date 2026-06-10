from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import exception_out, run_out
from app.db import get_db
from app.security import get_current_user

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=list[schemas.RunOut])
def list_runs(
    dataset_id: int | None = None,
    check_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    q = db.query(models.CheckRun)
    if dataset_id is not None:
        q = q.filter(models.CheckRun.dataset_id == dataset_id)
    if check_id is not None:
        q = q.filter(models.CheckRun.check_id == check_id)
    if status:
        q = q.filter(models.CheckRun.status == status)
    runs = q.order_by(models.CheckRun.id.desc()).offset(offset).limit(min(limit, 200)).all()
    return [run_out(db, r) for r in runs]


@router.get("/{run_id}", response_model=schemas.RunOut)
def get_run(run_id: int, db: Session = Depends(get_db), _: models.User = Depends(get_current_user)):
    run = db.get(models.CheckRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run_out(db, run)


@router.get("/{run_id}/exceptions", response_model=list[schemas.ExceptionOut])
def run_exceptions(
    run_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    excs = (
        db.query(models.ExceptionRecord)
        .filter(models.ExceptionRecord.run_id == run_id)
        .order_by(models.ExceptionRecord.id)
        .all()
    )
    return [exception_out(db, e) for e in excs]
