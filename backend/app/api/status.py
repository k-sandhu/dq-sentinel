"""Read-only data Status page API (D9 / #179).

A viewer-safe, stakeholder-facing summary: per-dataset health tiles + a recent
incident-update timeline. It reuses incident lifecycle data but exposes a deliberate
ALLOWLIST shape (``schemas.DataStatusOut``) — never ``external_refs``/``dedupe_key``,
event ``detail`` blobs, internal user names, ``pii_columns`` values, or any row-level
data. Grant-scoped to the caller's visible connections, identical to the rest of the
app (admin / zero-grant = unrestricted).

The optional unauthenticated/``?token=`` public view is a DEFERRED seam: when added it
must reuse the per-connection grant model in ``app.security`` (``visible_*_ids``), not
a parallel ACL. The authed page ships now.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user, visible_dataset_ids

router = APIRouter(prefix="/status", tags=["status"])

# Incident-event kinds safe to surface publicly. The internal ops events
# (escalated / notified / system) are dropped — they leak routing + notification
# detail and carry no stakeholder signal.
SAFE_UPDATE_KINDS = {"opened", "occurred", "acknowledged", "resolved", "recovered"}
UPDATES_CAP = 20
DATASETS_CAP = 200

# Worst-first ranking of the public health vocabulary (for tile sort + overall).
_HEALTH_RANK = {"degraded": 3, "delayed": 2, "unknown": 1, "operational": 0}


def _dataset_health(ds: models.Dataset) -> str:
    """Coarse public health from active checks' last_status — mirrors
    ``serialize.dataset_out`` (pass/warn/fail → operational/delayed/degraded)."""
    statuses = {c.last_status for c in ds.checks if c.status == "active" and c.last_status}
    if not statuses:
        return "unknown"
    if "fail" in statuses or "error" in statuses:
        return "degraded"
    if "warn" in statuses:
        return "delayed"
    return "operational"


@router.get("", response_model=schemas.DataStatusOut)
def data_status(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.DataStatusOut:
    visible_ds = visible_dataset_ids(db, user)  # Dataset.id subquery, or None = unrestricted

    # --- per-dataset incident aggregates (scoped) ---
    open_q = (
        db.query(models.Incident.dataset_id, func.count().label("n"))
        .filter(models.Incident.status.in_(("open", "acknowledged")))
        .group_by(models.Incident.dataset_id)
    )
    last_q = db.query(
        models.Incident.dataset_id, func.max(models.Incident.last_seen_at).label("last_at")
    ).group_by(models.Incident.dataset_id)
    if visible_ds is not None:
        open_q = open_q.filter(models.Incident.dataset_id.in_(visible_ds))
        last_q = last_q.filter(models.Incident.dataset_id.in_(visible_ds))
    open_by_ds = {r.dataset_id: int(r.n) for r in open_q.all()}
    last_by_ds = {r.dataset_id: r.last_at for r in last_q.all()}

    # --- health tiles for monitored datasets the caller may see ---
    ds_q = db.query(models.Dataset).options(joinedload(models.Dataset.checks))
    if visible_ds is not None:
        ds_q = ds_q.filter(models.Dataset.id.in_(visible_ds))

    tiles: list[schemas.StatusDatasetOut] = []
    counts = {"operational": 0, "delayed": 0, "degraded": 0, "unknown": 0}
    for ds in ds_q.all():
        if not any(c.status == "active" for c in ds.checks):
            continue  # only monitored datasets get a tile
        health = _dataset_health(ds)
        counts[health] += 1
        tiles.append(
            schemas.StatusDatasetOut(
                id=ds.id,
                name=ds.display_name or ds.table_name,
                health=health,  # type: ignore[arg-type]
                open_incidents=open_by_ds.get(ds.id, 0),
                last_incident_at=last_by_ds.get(ds.id),
            )
        )
    tiles.sort(key=lambda t: (-_HEALTH_RANK[t.health], t.name))
    tiles = tiles[:DATASETS_CAP]

    # overall = worst present state; unknown only when nothing is op/delayed/degraded
    overall = "unknown"
    for state in ("degraded", "delayed", "operational", "unknown"):
        if counts.get(state):
            overall = state
            break

    # --- recent incident-update timeline (safe kinds only, scoped) ---
    ev_q = (
        db.query(models.IncidentEvent, models.Incident, models.Dataset)
        .join(models.Incident, models.IncidentEvent.incident_id == models.Incident.id)
        .join(models.Dataset, models.Incident.dataset_id == models.Dataset.id)
        .filter(models.IncidentEvent.kind.in_(SAFE_UPDATE_KINDS))
        .order_by(models.IncidentEvent.created_at.desc(), models.IncidentEvent.id.desc())
    )
    if visible_ds is not None:
        ev_q = ev_q.filter(models.Incident.dataset_id.in_(visible_ds))
    updates = [
        schemas.StatusUpdateOut(
            kind=ev.kind,
            title=inc.title,
            dataset_name=ds.display_name or ds.table_name,
            severity=inc.severity,  # type: ignore[arg-type]
            at=ev.created_at,
        )
        for ev, inc, ds in ev_q.limit(UPDATES_CAP).all()
    ]

    return schemas.DataStatusOut(
        overall=overall,  # type: ignore[arg-type]
        operational=counts["operational"],
        delayed=counts["delayed"],
        degraded=counts["degraded"],
        datasets=tiles,
        updates=updates,
        generated_at=utcnow(),
    )
