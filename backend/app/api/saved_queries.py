"""Saved queries: a team-shared library of workbench SQL (issue #41).

Saved across the team server-side (not localStorage); any authenticated user can
list/read them. Creating/editing requires editor role, and edits/deletes are
limited to the creator or an admin. SQL is validated through guard_sql() at SAVE
time so the library can't accumulate non-SELECT junk. Running a saved query
delegates to the same execution helper as the workbench's POST /query/run.

"Shared across the team" is scoped by the per-connection grant model (#72): a
saved query is connection-bound, so reads are restricted to connections the
caller may see, and RUNNING one — which executes persisted SQL against the source
— requires editor ON THAT CONNECTION, exactly like POST /query/run. A global
editor role is not enough; being able to save a query must never become a way to
execute SQL against a source you were never granted."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.api._filters import LIKE_ESCAPE, contains_pattern
from app.api.query import execute_select
from app.connectors.sa import connector_for
from app.connectors.safety import SqlNotAllowed, guard_sql
from app.db import get_db
from app.models import utcnow
from app.security import (
    assert_connection_role,
    get_current_user,
    require_role,
    visible_connection_ids,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/queries", tags=["saved-queries"])


def _serialize(db: Session, q: models.SavedQuery) -> schemas.SavedQueryOut:
    out = schemas.SavedQueryOut.model_validate(q)
    if q.created_by_id:
        user = db.get(models.User, q.created_by_id)
        out.created_by = (user.name or user.email) if user else None
    return out


def _guard_or_422(sql: str) -> str:
    try:
        return guard_sql(sql)
    except SqlNotAllowed as exc:
        raise HTTPException(422, str(exc)) from exc


def _require_owner_or_admin(q: models.SavedQuery, user: models.User) -> None:
    if user.role != "admin" and q.created_by_id != user.id:
        raise HTTPException(403, "Only the creator or an admin can modify this query")


def _get_visible(db: Session, user: models.User, query_id: int) -> models.SavedQuery:
    """Fetch a saved query whose connection the caller may see, else 404 (#72).

    A missing query and one on an ungranted connection return an IDENTICAL 404
    body, so sequential id probing can't reveal that a query exists elsewhere.
    Membership is tested against ``visible_connection_ids`` rather than
    ``connection_role`` so an orphaned query (its connection was deleted) still
    resolves and can report the honest 409 below instead of a misleading 404.
    """
    q = db.get(models.SavedQuery, query_id)
    if q is None:
        raise HTTPException(404, "Saved query not found")
    vis = visible_connection_ids(db, user)  # None -> unrestricted (admin / zero-grant)
    if vis is not None and q.connection_id not in vis:
        raise HTTPException(404, "Saved query not found")
    return q


@router.get("", response_model=list[schemas.SavedQueryOut])
def list_saved_queries(
    connection_id: int | None = None,
    dataset_id: int | None = None,
    tag: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Shared across the team by design — any authenticated user can browse the
    queries on connections they can see (#72).

    Filters: connection_id, dataset_id, tag (membership), q (name/description
    ilike). Sorted updated_at desc. Kept lean — sibling #43's cmd-K consumes ?q=."""
    query = db.query(models.SavedQuery)
    vis = visible_connection_ids(db, user)
    if vis is not None:  # restrict to granted connections (admin / zero-grant -> all)
        query = query.filter(models.SavedQuery.connection_id.in_(vis))
    if connection_id is not None:
        query = query.filter(models.SavedQuery.connection_id == connection_id)
    if dataset_id is not None:
        query = query.filter(models.SavedQuery.dataset_id == dataset_id)
    if q:
        # Escaped: a typed % or _ is literal text, not a wildcard (#282/#273).
        like = contains_pattern(q.strip())
        query = query.filter(
            models.SavedQuery.name.ilike(like, escape=LIKE_ESCAPE)
            | models.SavedQuery.description.ilike(like, escape=LIKE_ESCAPE)
        )
    rows = query.order_by(models.SavedQuery.updated_at.desc()).limit(200).all()
    # Tag membership is filtered in Python: JSON-array containment isn't portable
    # across SQLite (dev) and Postgres (prod), and the result set is already small.
    if tag:
        rows = [r for r in rows if tag in (r.tags or [])]
    return [_serialize(db, r) for r in rows]


@router.get("/{query_id}", response_model=schemas.SavedQueryOut)
def get_saved_query(
    query_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Fetch one shared saved query (used by the workbench saved_query_id deep-link).

    Scoped to visible connections (#72) — the response carries the stored SQL,
    which describes the shape of a source the caller may not be granted."""
    return _serialize(db, _get_visible(db, user, query_id))


@router.post("", response_model=schemas.SavedQueryOut, status_code=201)
def create_saved_query(
    body: schemas.SavedQueryIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    # Editor ON THIS connection (#72): saving is how SQL gets persisted for later
    # server-side execution, so it needs the same authority as running it.
    assert_connection_role(db, user, body.connection_id, "editor")
    if body.dataset_id is not None:
        ds = db.get(models.Dataset, body.dataset_id)
        if ds is None:
            raise HTTPException(404, "Dataset not found")
        if ds.connection_id != body.connection_id:
            raise HTTPException(422, "dataset_id does not belong to connection_id")
    cleaned = _guard_or_422(body.sql)
    q = models.SavedQuery(
        connection_id=body.connection_id,
        dataset_id=body.dataset_id,
        name=body.name.strip(),
        description=body.description,
        sql=cleaned,
        tags=[t.strip() for t in body.tags if t.strip()],
        created_by_id=user.id,
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return _serialize(db, q)


@router.patch("/{query_id}", response_model=schemas.SavedQueryOut)
def update_saved_query(
    query_id: int,
    body: schemas.SavedQueryUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    q = _get_visible(db, user, query_id)  # 404 for missing OR ungranted (#72)
    _require_owner_or_admin(q, user)
    if body.name is not None:
        q.name = body.name.strip()
    if body.description is not None:
        q.description = body.description
    if body.sql is not None:
        q.sql = _guard_or_422(body.sql)  # re-validate edited SQL at save time
    if body.tags is not None:
        q.tags = [t.strip() for t in body.tags if t.strip()]
    if body.unpin:
        q.dataset_id = None
    elif body.dataset_id is not None:
        ds = db.get(models.Dataset, body.dataset_id)
        if ds is None:
            raise HTTPException(404, "Dataset not found")
        if ds.connection_id != q.connection_id:
            raise HTTPException(422, "dataset_id does not belong to this query's connection")
        q.dataset_id = body.dataset_id
    db.commit()
    db.refresh(q)
    return _serialize(db, q)


@router.delete("/{query_id}", status_code=204)
def delete_saved_query(
    query_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    q = _get_visible(db, user, query_id)  # 404 for missing OR ungranted (#72)
    _require_owner_or_admin(q, user)
    db.delete(q)
    db.commit()


@router.post("/{query_id}/run", response_model=schemas.QueryRunOut)
def run_saved_query(
    query_id: int,
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Execute a persisted query against its source.

    This is an execution path, not a read, so it carries the SAME gate as POST
    /query/run: editor ON THIS connection (#72). The previous global-editor check
    let any editor run arbitrary stored SQL against a connection they were never
    granted. 404 when the query or its connection isn't visible (existence isn't
    leaked); 403 only when the connection is visible but the grant is viewer-only.
    """
    q = _get_visible(db, user, query_id)
    conn = db.get(models.Connection, q.connection_id)
    if conn is None:
        # Visible (per the grant set) but orphaned — say so honestly rather than 404.
        raise HTTPException(409, "This query's connection no longer exists")
    assert_connection_role(db, user, q.connection_id, "editor")
    result = execute_select(connector_for(conn), q.sql, limit)
    q.last_run_at = utcnow()
    db.commit()
    return result
