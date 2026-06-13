from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import exception_event_out, exception_out
from app.core.events import record_event
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user, require_role

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

# Bulk-operation cap (#56): bounds transaction size + event-write amplification.
MAX_BULK_IDS = 1000


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
    if len(body.ids) > MAX_BULK_IDS:
        raise HTTPException(422, f"Too many ids (max {MAX_BULK_IDS})")
    # 422 if the body would do nothing.
    if body.status is None and not body.note and body.assigned_to_id is None and not body.clear_assignee:
        raise HTTPException(422, "Nothing to do: provide a status, note, or assignment change")
    # Validate a new assignee references an active user (deactivated users keep
    # existing assignments but cannot receive new ones).
    if body.assigned_to_id is not None:
        target = db.get(models.User, body.assigned_to_id)
        if target is None or not target.is_active:
            raise HTTPException(422, "Assignee must be an active user")

    excs = db.query(models.ExceptionRecord).filter(models.ExceptionRecord.id.in_(body.ids)).all()
    if not excs:
        raise HTTPException(404, "No matching exceptions found")

    now = utcnow()
    for e in excs:
        touched = False
        # Status change (+ event) only when it actually differs.
        if body.status is not None and body.status != e.status:
            old = e.status
            e.status = body.status
            record_event(
                db, e, "status", user_id=user.id,
                from_status=old, to_status=body.status, comment=body.note,
            )
            touched = True
        # Assignment change (+ event); assignee name goes in the comment.
        if body.assigned_to_id is not None:
            e.assigned_to_id = body.assigned_to_id
            name = _display_name_for(db, body.assigned_to_id)
            record_event(db, e, "assign", user_id=user.id, comment=f"assigned to {name}")
            touched = True
        elif body.clear_assignee:
            e.assigned_to_id = None
            record_event(db, e, "assign", user_id=user.id, comment="unassigned")
            touched = True
        # A note with no status/assignment change is a standalone comment event.
        if body.note and not touched:
            record_event(db, e, "comment", user_id=user.id, comment=body.note)
        # Back-compat: `note` mirrors the latest note; marked_by/at semantics kept.
        if body.note:
            e.note = body.note
        if touched or body.note:
            e.marked_by_id = user.id
            e.marked_at = now
    db.commit()
    return [exception_out(db, e) for e in excs]


def _display_name_for(db: Session, user_id: int) -> str:
    u = db.get(models.User, user_id)
    return (u.name or u.email) if u else "unknown"


@router.get("/{exc_id}/events", response_model=list[schemas.ExceptionEventOut])
def list_events(
    exc_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),  # any authenticated user (viewers included)
):
    if db.get(models.ExceptionRecord, exc_id) is None:
        raise HTTPException(404, "Exception not found")
    events = (
        db.query(models.ExceptionEvent)
        .filter(models.ExceptionEvent.exception_id == exc_id)
        .order_by(models.ExceptionEvent.id)
        .all()
    )
    return [exception_event_out(db, ev) for ev in events]


@router.post("/{exc_id}/comments", response_model=schemas.ExceptionEventOut, status_code=201)
def add_comment(
    exc_id: int,
    body: schemas.CommentIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    exc = db.get(models.ExceptionRecord, exc_id)
    if exc is None:
        raise HTTPException(404, "Exception not found")
    ev = record_event(db, exc, "comment", user_id=user.id, comment=body.comment)
    exc.note = body.comment  # latest-note convenience
    db.commit()
    db.refresh(ev)
    return exception_event_out(db, ev)
