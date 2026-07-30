"""Read-only audit-log viewer (issue #30). Admin-only.

Rows are written in-band by ``app.core.audit.audit()`` at the instrumented call
sites; there is no write endpoint here (the trail is append-only and never
mutated through the API).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.api._filters import LIKE_ESCAPE, escape_like
from app.db import get_db
from app.security import require_role

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=schemas.AuditPage)
def list_audit(
    entity_type: str | None = None,
    action: str | None = None,
    user_id: int | None = None,
    since: datetime | None = None,
    q: str | None = Query(default=None, description="action prefix match, e.g. 'login'"),
    limit: int = Query(default=50, ge=1, le=200),
    # Already rejected a negative page; the sweep in #274 added the upper bound
    # the other list endpoints now share.
    offset: int = Query(default=0, ge=0, le=schemas.MAX_OFFSET),
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("admin")),
):
    query = db.query(models.AuditEntry)
    if entity_type:
        query = query.filter(models.AuditEntry.entity_type == entity_type)
    if action:
        query = query.filter(models.AuditEntry.action == action)
    if user_id is not None:
        query = query.filter(models.AuditEntry.user_id == user_id)
    if since is not None:
        query = query.filter(models.AuditEntry.created_at >= since)
    if q:
        # PREFIX match (documented above), with the needle's own %/_ escaped so a
        # typed wildcard is literal text (#282/#273). Deliberately NOT
        # contains_pattern(): a leading "%" would silently widen this to a
        # substring search over the compliance trail.
        query = query.filter(
            func.lower(models.AuditEntry.action).like(
                f"{escape_like(q.lower())}%", escape=LIKE_ESCAPE
            )
        )

    total = query.count()
    rows = query.order_by(models.AuditEntry.id.desc()).offset(offset).limit(limit).all()

    # Resolve user display names in one query (avoid N+1).
    user_ids = {r.user_id for r in rows if r.user_id is not None}
    names: dict[int, str] = {}
    if user_ids:
        for u in db.query(models.User).filter(models.User.id.in_(user_ids)).all():
            names[u.id] = u.name or u.email

    items = []
    for r in rows:
        out = schemas.AuditEntryOut.model_validate(r)
        out.user = names.get(r.user_id) if r.user_id is not None else None
        items.append(out)

    return schemas.AuditPage(items=items, total=total, limit=limit, offset=offset)
