from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import exception_out
from app.core.audit import audit
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user, require_role

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


@router.get("", response_model=list[schemas.ExceptionOut])
def list_exceptions(
    response: Response,
    dataset_id: int | None = None,
    check_id: int | None = None,
    run_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    query = db.query(models.ExceptionRecord)
    if dataset_id is not None:
        query = query.filter(models.ExceptionRecord.dataset_id == dataset_id)
    if check_id is not None:
        query = query.filter(models.ExceptionRecord.check_id == check_id)
    if run_id is not None:
        query = query.filter(models.ExceptionRecord.run_id == run_id)
    if status:
        query = query.filter(models.ExceptionRecord.status == status)
    if q:
        needle = f"%{q.lower()}%"
        query = query.filter(
            func.lower(models.ExceptionRecord.reason).like(needle)
            | func.lower(models.ExceptionRecord.note).like(needle)
        )
    response.headers["X-Total-Count"] = str(query.count())
    excs = (
        query.order_by(models.ExceptionRecord.id.desc()).offset(offset).limit(min(limit, 500)).all()
    )
    return [exception_out(db, e) for e in excs]


@router.post("/triage", response_model=list[schemas.ExceptionOut])
def triage(
    body: schemas.TriageIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    if not body.ids:
        raise HTTPException(422, "No exception ids provided")
    excs = (
        db.query(models.ExceptionRecord).filter(models.ExceptionRecord.id.in_(body.ids)).all()
    )
    if not excs:
        raise HTTPException(404, "No matching exceptions found")
    for e in excs:
        e.status = body.status
        if body.note:
            e.note = body.note
        e.marked_by_id = user.id
        e.marked_at = utcnow()
    # One audit row per batch (ExceptionEvent has the per-row record).
    audit(db, user, "exception.triage", "exception", None, count=len(excs), status=body.status)
    db.commit()
    return [exception_out(db, e) for e in excs]
