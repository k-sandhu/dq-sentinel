"""Unified global search across entities — backs the command-K palette (issue #43).

Four small independent ILIKE queries (datasets, checks, connections, saved
queries), each capped at `limit`. No search engine, no new deps. Hits are
ordered exact-prefix-first, then by entity type in a fixed order.

Saved queries come from issue #41's table, which may not exist yet — that query
is wrapped in try/except and skipped silently when the table is absent.

Connection-grant scoping (#159): every connection-bound branch is filtered by the
caller's visible connections, so a granted user cannot discover datasets / checks
/ connections on sources they weren't granted (admin and zero-grant users see all).
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
from app.security import get_current_user, visible_connection_ids

log = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])

# Stable ordering of entity types when relevance (exact-prefix) is equal.
_TYPE_ORDER = {"dataset": 0, "check": 1, "connection": 2, "saved_query": 3}


def _rank(hit: schemas.SearchHit, needle: str) -> tuple[int, int]:
    """Exact-prefix matches first (0), then by entity type order."""
    prefix = 0 if hit.title.lower().startswith(needle) else 1
    return (prefix, _TYPE_ORDER.get(hit.type, 99))


@router.get("", response_model=schemas.SearchOut)
def search(
    q: str = "",
    limit: int = 5,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.SearchOut:
    needle = q.strip().lower()
    if not needle:
        return schemas.SearchOut(hits=[])
    limit = max(1, min(limit, 25))
    like = f"%{needle}%"
    hits: list[schemas.SearchHit] = []
    vis = visible_connection_ids(db, user)  # None -> unrestricted (admin / zero-grant) (#159)

    # datasets: match table_name / display_name; subtitle = connection name.
    ds_q = (
        db.query(models.Dataset, models.Connection.name)
        .join(models.Connection, models.Dataset.connection_id == models.Connection.id)
        .filter(
            func.lower(models.Dataset.table_name).like(like)
            | func.lower(func.coalesce(models.Dataset.display_name, "")).like(like)
        )
    )
    if vis is not None:
        ds_q = ds_q.filter(models.Dataset.connection_id.in_(vis))
    dataset_rows = ds_q.order_by(models.Dataset.table_name).limit(limit).all()
    for ds, conn_name in dataset_rows:
        title = f"{ds.schema_name}.{ds.table_name}" if ds.schema_name else ds.table_name
        hits.append(
            schemas.SearchHit(
                type="dataset",
                id=ds.id,
                title=title,
                subtitle=conn_name,
                url=f"/datasets/{ds.id}",
            )
        )

    # checks: match name; subtitle = dataset table_name; url = dataset's Checks tab.
    chk_q = (
        db.query(models.Check, models.Dataset.table_name)
        .join(models.Dataset, models.Check.dataset_id == models.Dataset.id)
        .filter(
            models.Check.status != "archived",
            func.lower(models.Check.name).like(like),
        )
    )
    if vis is not None:
        chk_q = chk_q.filter(models.Dataset.connection_id.in_(vis))
    check_rows = chk_q.order_by(models.Check.name).limit(limit).all()
    for chk, table_name in check_rows:
        hits.append(
            schemas.SearchHit(
                type="check",
                id=chk.id,
                title=chk.name,
                subtitle=table_name,
                url=f"/datasets/{chk.dataset_id}/checks",
            )
        )

    # connections: match name; subtitle = kind.
    conn_q = db.query(models.Connection).filter(func.lower(models.Connection.name).like(like))
    if vis is not None:
        conn_q = conn_q.filter(models.Connection.id.in_(vis))
    conn_rows = conn_q.order_by(models.Connection.name).limit(limit).all()
    for conn in conn_rows:
        hits.append(
            schemas.SearchHit(
                type="connection",
                id=conn.id,
                title=conn.name,
                subtitle=conn.kind,
                url="/connections",
            )
        )

    # saved queries (issue #41): the table may not exist yet — skip silently.
    hits.extend(_saved_query_hits(db, like, limit, vis))

    hits.sort(key=lambda h: _rank(h, needle))
    return schemas.SearchOut(hits=hits)


def _saved_query_hits(
    db: Session, like: str, limit: int, vis: set[int] | None
) -> list[schemas.SearchHit]:
    """Saved-query hits from issue #41's `saved_queries` table.

    Feature-detected at runtime: the table won't exist in worktrees built before
    #41 lands, so a missing-table error is caught and the section skipped.

    Connection-grant scoped (#159): saved queries are connection-bound, so a granted
    user must not discover their names/ids (or the workbench deep-link) for
    connections they can't access. ``vis is None`` -> unrestricted (admin / zero-grant).
    """
    sql = "SELECT id, name FROM saved_queries WHERE lower(name) LIKE :like"
    params: dict = {"like": like, "limit": limit}
    if vis is not None:
        if not vis:
            return []
        placeholders = ", ".join(f":c{i}" for i in range(len(vis)))
        sql += f" AND connection_id IN ({placeholders})"
        for i, cid in enumerate(vis):
            params[f"c{i}"] = cid
    sql += " ORDER BY name LIMIT :limit"
    try:
        rows = db.execute(text(sql), params).all()
    except SQLAlchemyError:
        db.rollback()  # clear the failed transaction so later queries still work
        return []
    return [
        schemas.SearchHit(
            type="saved_query",
            id=row.id,
            title=row.name,
            subtitle="Saved query",
            url=f"/workbench?saved_query_id={row.id}",
        )
        for row in rows
    ]
