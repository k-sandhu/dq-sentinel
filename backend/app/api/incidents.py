"""Authenticated incident lifecycle APIs."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.incidents import acknowledge_incident, resolve_incident
from app.db import get_db
from app.security import (
    assert_dataset_visible,
    get_current_user,
    require_role,
    visible_dataset_ids,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _display_name(user: models.User | None) -> str | None:
    if user is None:
        return None
    return user.name or user.email


def _incident_out(db: Session, incident: models.Incident) -> schemas.IncidentOut:
    out = schemas.IncidentOut.model_validate(incident)
    ds = db.get(models.Dataset, incident.dataset_id)
    if ds is not None:
        out.dataset_name = ds.display_name or ds.table_name
    check = db.get(models.Check, incident.check_id)
    if check is not None:
        out.check_name = check.name
    return out


def _event_out(db: Session, ev: models.IncidentEvent) -> schemas.IncidentEventOut:
    out = schemas.IncidentEventOut.model_validate(ev)
    out.user = _display_name(db.get(models.User, ev.user_id)) if ev.user_id else None
    return out


@router.get("", response_model=list[schemas.IncidentOut])
def list_incidents(
    response: Response,
    status: str | None = None,
    dataset_id: int | None = None,
    check_id: int | None = None,
    severity: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    query = db.query(models.Incident)
    # Connection-grant scoping (#179): restrict to incidents on datasets the caller
    # may see. None -> unrestricted (admin / zero-grant legacy).
    visible_ds = visible_dataset_ids(db, user)
    if visible_ds is not None:
        query = query.filter(models.Incident.dataset_id.in_(visible_ds))
    if status:
        query = query.filter(models.Incident.status == status)
    if dataset_id is not None:
        query = query.filter(models.Incident.dataset_id == dataset_id)
    if check_id is not None:
        query = query.filter(models.Incident.check_id == check_id)
    if severity:
        query = query.filter(models.Incident.severity == severity)
    response.headers["X-Total-Count"] = str(query.count())
    incidents = (
        query.order_by(models.Incident.last_seen_at.desc(), models.Incident.id.desc())
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return [_incident_out(db, incident) for incident in incidents]


@router.get("/{incident_id}", response_model=schemas.IncidentDetailOut)
def get_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    incident = db.get(models.Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    # Grant scoping (#179): 404 (not 403) when the incident's dataset is invisible,
    # so an out-of-scope incident's existence isn't leaked.
    assert_dataset_visible(db, user, incident.dataset_id)
    out = schemas.IncidentDetailOut(**_incident_out(db, incident).model_dump())
    out.events = [_event_out(db, ev) for ev in incident.events]
    return out


@router.post("/{incident_id}/ack", response_model=schemas.IncidentDetailOut)
def ack_incident(
    incident_id: int,
    body: schemas.IncidentActionIn | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    incident = db.get(models.Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    acknowledge_incident(db, incident, user, body.note if body else "")
    return get_incident(incident_id, db, user)


@router.post("/{incident_id}/resolve", response_model=schemas.IncidentDetailOut)
def resolve_incident_endpoint(
    incident_id: int,
    body: schemas.IncidentActionIn | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    incident = db.get(models.Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    resolve_incident(db, incident, user, body.note if body else "")
    return get_incident(incident_id, db, user)
