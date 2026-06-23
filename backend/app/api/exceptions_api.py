import csv
import io
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func
from sqlalchemy.orm import Query as SAQuery
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import exception_event_out, exception_out
from app.core.audit import audit
from app.core.events import record_event
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user, require_role

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

# Bulk-operation cap (#56): bounds transaction size + event-write amplification.
MAX_BULK_IDS = 1000
# Data-egress control (#57): exports are capped, not just for performance.
EXPORT_CAP = 10_000
SORT_OPTIONS = {"newest", "oldest", "occurrences", "severity"}
SEVERITY_ORDER = case(
    (models.Check.severity == "error", 0),
    (models.Check.severity == "warn", 1),
    else_=2,
)


def _filtered(
    db: Session,
    *,
    dataset_id: int | None = None,
    check_id: int | None = None,
    run_id: int | None = None,
    status: list[str] | None = None,
    severity: list[str] | None = None,
    check_type: str | None = None,
    assignee: str | None = None,
    recurrence: str | None = None,
    seen_since: datetime | None = None,
    q: str | None = None,
    current_user: models.User | None = None,
) -> SAQuery:
    """Shared filter construction for list / facets / export (#57).

    Joins Check so severity/check_type/check-name filters work. Every common
    combination starts from an indexed column ((dataset_id, status),
    (status, last_seen_at), (check_id, fingerprint)).
    """
    query = db.query(models.ExceptionRecord).join(
        models.Check, models.Check.id == models.ExceptionRecord.check_id
    )
    if dataset_id is not None:
        query = query.filter(models.ExceptionRecord.dataset_id == dataset_id)
    if check_id is not None:
        query = query.filter(models.ExceptionRecord.check_id == check_id)
    if run_id is not None:
        query = query.filter(models.ExceptionRecord.run_id == run_id)
    if status:
        query = query.filter(models.ExceptionRecord.status.in_(status))
    if severity:
        query = query.filter(models.Check.severity.in_(severity))
    if check_type:
        query = query.filter(models.Check.check_type == check_type)
    if assignee:
        if assignee == "me" and current_user is not None:
            query = query.filter(models.ExceptionRecord.assigned_to_id == current_user.id)
        elif assignee == "none":
            query = query.filter(models.ExceptionRecord.assigned_to_id.is_(None))
        elif assignee.isdigit():
            query = query.filter(models.ExceptionRecord.assigned_to_id == int(assignee))
    if recurrence == "new":
        query = query.filter(
            models.ExceptionRecord.first_seen_at >= utcnow() - timedelta(hours=24)
        )
    elif recurrence == "recurring":
        query = query.filter(models.ExceptionRecord.occurrence_count >= 2)
    if seen_since is not None:
        query = query.filter(models.ExceptionRecord.last_seen_at >= seen_since)
    if q:
        needle = f"%{q.lower()}%"
        query = query.filter(
            func.lower(models.ExceptionRecord.reason).like(needle)
            | func.lower(models.ExceptionRecord.note).like(needle)
            | func.lower(models.Check.name).like(needle)
        )
    return query


def _apply_sort(query: SAQuery, sort: str) -> SAQuery:
    """Every sort appends `id desc` as a deterministic tie-breaker — unstable
    pagination that drops/dupes rows across pages destroys analyst trust (#57)."""
    exc = models.ExceptionRecord
    if sort == "oldest":
        return query.order_by(exc.id.asc())
    if sort == "occurrences":
        return query.order_by(exc.occurrence_count.desc(), exc.id.desc())
    if sort == "severity":
        return query.order_by(SEVERITY_ORDER, exc.id.desc())
    return query.order_by(exc.id.desc())  # newest (default)


# Repeatable query params are declared once here and reused across endpoints.
def _common_filters(
    dataset_id: int | None = None,
    check_id: int | None = None,
    run_id: int | None = None,
    status: list[str] | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    check_type: str | None = None,
    assignee: str | None = None,
    recurrence: str | None = None,
    seen_since: datetime | None = None,
    q: str | None = None,
) -> dict:
    return {
        "dataset_id": dataset_id,
        "check_id": check_id,
        "run_id": run_id,
        "status": status,
        "severity": severity,
        "check_type": check_type,
        "assignee": assignee,
        "recurrence": recurrence,
        "seen_since": seen_since,
        "q": q,
    }


@router.get("", response_model=schemas.ExceptionPage)
def list_exceptions(
    filters: dict = Depends(_common_filters),
    sort: str = "newest",
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    query = _filtered(db, current_user=user, **filters)
    total = query.count()
    limit = min(max(limit, 1), 500)
    excs = _apply_sort(query, sort).offset(offset).limit(limit).all()
    return schemas.ExceptionPage(
        items=[exception_out(db, e) for e in excs], total=total, limit=limit, offset=offset
    )


@router.get("/facets", response_model=schemas.ExceptionFacets)
def exception_facets(
    filters: dict = Depends(_common_filters),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Counts for the filter bar + saved-view badges. Each facet excludes its OWN
    dimension (standard faceted search) but comes from the SAME filter snapshot as
    the list total — mismatched chip/table counts erode daily-user trust (#57)."""

    def grouped(column, *, drop: str) -> dict:
        f = {**filters, drop: None}
        rows = (
            _filtered(db, current_user=user, **f)
            .with_entities(column, func.count(models.ExceptionRecord.id))
            .group_by(column)
            .all()
        )
        return {str(k): int(v) for k, v in rows if k is not None}

    status = grouped(models.ExceptionRecord.status, drop="status")
    severity = grouped(models.Check.severity, drop="severity")
    check_type = grouped(models.Check.check_type, drop="check_type")

    # Datasets facet: count by dataset, top 20 by count. (No own-dimension to
    # drop here — dataset filtering is by id and rarely multi-select in v1.)
    ds_rows = (
        _filtered(db, current_user=user, **filters)
        .with_entities(
            models.ExceptionRecord.dataset_id,
            models.Dataset.table_name,
            func.count(models.ExceptionRecord.id),
        )
        .join(models.Dataset, models.Dataset.id == models.ExceptionRecord.dataset_id)
        .group_by(models.ExceptionRecord.dataset_id, models.Dataset.table_name)
        .order_by(func.count(models.ExceptionRecord.id).desc())
        .limit(20)
        .all()
    )
    datasets = [
        schemas.FacetEntry(id=int(did), name=name or "", count=int(c)) for did, name, c in ds_rows
    ]
    total = _filtered(db, current_user=user, **filters).count()
    return schemas.ExceptionFacets(
        status=status, severity=severity, check_type=check_type, datasets=datasets, total=total
    )


def _csv_safe(value) -> str:
    """Neutralize spreadsheet formula injection: prefix a leading '=','+','-','@',
    tab or CR with a single quote before Excel/Sheets can evaluate it (#57)."""
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def _audit_safe_filters(filters: dict) -> dict:
    safe: dict = {}
    for key, value in filters.items():
        if value in (None, [], ""):
            continue
        if isinstance(value, datetime):
            safe[key] = value.isoformat()
        elif key == "q":
            safe[key] = {"present": True, "length": len(value)}
        else:
            safe[key] = value
    return safe


def _audit_safe_sort(sort: str) -> str:
    return sort if sort in SORT_OPTIONS else "newest"


@router.get("/export.csv")
def export_csv(
    filters: dict = Depends(_common_filters),
    sort: str = "newest",
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    base_query = _filtered(db, current_user=user, **filters)
    matching_count = base_query.count()
    query = _apply_sort(base_query, sort).limit(EXPORT_CAP)
    rows = query.all()
    # Resolve display fields without N+1 per row.
    ds_names = {d.id: d.table_name for d in db.query(models.Dataset).all()}
    user_names = {u.id: (u.name or u.email) for u in db.query(models.User).all()}
    checks = {
        c.id: c
        for c in db.query(models.Check.id, models.Check.name, models.Check.check_type, models.Check.severity)
    }
    audit(
        db,
        user,
        "exception.export",
        "exception",
        None,
        filters=_audit_safe_filters(filters),
        sort=_audit_safe_sort(sort),
        matching_count=matching_count,
        exported_count=len(rows),
        export_cap=EXPORT_CAP,
        truncated=matching_count > len(rows),
    )
    db.commit()
    columns = [
        "id", "dataset", "check", "check_type", "severity", "status", "reason",
        "occurrence_count", "first_seen_at", "last_seen_at", "assigned_to", "note", "row_data",
    ]

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for e in rows:
            check = checks.get(e.check_id)
            writer.writerow(
                [
                    e.id,
                    _csv_safe(ds_names.get(e.dataset_id, "")),
                    _csv_safe(check.name if check else ""),
                    _csv_safe(check.check_type if check else ""),
                    _csv_safe(check.severity if check else ""),
                    _csv_safe(e.status),
                    _csv_safe(e.reason),
                    e.occurrence_count,
                    e.first_seen_at.isoformat() if e.first_seen_at else "",
                    e.last_seen_at.isoformat() if e.last_seen_at else "",
                    _csv_safe(user_names.get(e.assigned_to_id, "")),
                    _csv_safe(e.note),
                    _csv_safe(json.dumps(e.row_data, default=str)),
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=exceptions.csv"},
    )


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

    # Lock the rows so the optimistic-concurrency check is atomic (#156). FOR
    # UPDATE is a no-op on SQLite (which serializes writers anyway); on Postgres
    # a concurrent triager blocks until we commit, then sees the bumped version.
    excs = (
        db.query(models.ExceptionRecord)
        .filter(models.ExceptionRecord.id.in_(body.ids))
        .with_for_update()
        .all()
    )
    if not excs:
        raise HTTPException(404, "No matching exceptions found")

    # Optimistic concurrency: when the client sends the versions it last read,
    # reject the whole batch if any listed row changed underneath it (HTTP 409)
    # so a stale UI / concurrent triager can't silently clobber analyst state.
    if body.expected_versions:
        # Fail closed: once the client opts into version checking it must cover
        # every row it's mutating — a partial map would leave the omitted rows
        # unguarded. (Omitting expected_versions entirely stays backward-compatible.)
        missing = sorted(e.id for e in excs if e.id not in body.expected_versions)
        if missing:
            raise HTTPException(
                422,
                {
                    "message": "expected_versions must include every id being triaged",
                    "missing_ids": missing,
                },
            )
        conflicts = sorted(
            e.id for e in excs if body.expected_versions[e.id] != e.version
        )
        if conflicts:
            raise HTTPException(
                409,
                {
                    "message": "Some exceptions changed since you loaded them; refresh and retry",
                    "conflict_ids": conflicts,
                },
            )

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
            e.version = (e.version or 1) + 1  # optimistic-concurrency bump (#156)
    # One audit row per batch (#30); ExceptionEvent has the per-row record (#56).
    audit(db, user, "exception.triage", "exception", None, count=len(excs), status=body.status)
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
    # Lock + optimistic concurrency: a standalone comment mutates `note` — the
    # same field triage edits — so it must bump `version` (and may check an
    # expected version) or a concurrent triager would clobber the latest note (#156).
    exc = (
        db.query(models.ExceptionRecord)
        .filter(models.ExceptionRecord.id == exc_id)
        .with_for_update()
        .one_or_none()
    )
    if exc is None:
        raise HTTPException(404, "Exception not found")
    if body.expected_version is not None and body.expected_version != exc.version:
        raise HTTPException(
            409,
            {"message": "Exception changed since you loaded it; refresh and retry",
             "conflict_ids": [exc.id]},
        )
    ev = record_event(db, exc, "comment", user_id=user.id, comment=body.comment)
    exc.note = body.comment  # latest-note convenience
    exc.version = (exc.version or 1) + 1  # note changed -> optimistic-concurrency bump (#156)
    db.commit()
    db.refresh(ev)
    return exception_event_out(db, ev)
