"""Authenticated incident lifecycle APIs."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.incidents import acknowledge_incident, resolve_incident
from app.db import get_db
from app.security import (
    assert_connection_role,
    assert_dataset_visible,
    get_current_user,
    require_role,
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
    _: models.User = Depends(get_current_user),
):
    query = db.query(models.Incident)
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
    _: models.User = Depends(get_current_user),
):
    incident = db.get(models.Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    out = schemas.IncidentDetailOut(**_incident_out(db, incident).model_dump())
    out.events = [_event_out(db, ev) for ev in incident.events]
    return out


def _load_incident_for_mutation(
    db: Session, user: models.User, incident_id: int
) -> models.Incident:
    """Fetch an incident and enforce object-level authz before any mutation.

    The global ``require_role("editor")`` dependency only checks the caller's
    *global* role, not their per-connection grant — so without this a grant-scoped
    editor on connection A could ack/resolve an incident on connection B (#159/#179).
    404 for a missing incident or one on a connection the user can't see (don't leak
    existence); 403 for a visible connection where the grant role is below editor.
    ack/resolve mutate state, so require effective EDITOR on the connection, matching
    exception triage (see ``assert_connection_role`` and
    test_authz_enforcement.py::test_exception_mutation_requires_editor_on_connection).
    """
    incident = db.get(models.Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    # Normalize the authz 404s to the SAME detail as a missing incident so the
    # message can't distinguish "doesn't exist" from "exists but you can't see it"
    # (the helpers raise a generic "Not found"). The 403 for a visible connection
    # with an insufficient grant passes through unchanged (#159/#179, PR #204 review).
    try:
        ds = assert_dataset_visible(db, user, incident.dataset_id)
        assert_connection_role(db, user, ds.connection_id, "editor")
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(404, "Incident not found") from exc
        raise
    return incident


@router.post("/{incident_id}/ack", response_model=schemas.IncidentDetailOut)
def ack_incident(
    incident_id: int,
    body: schemas.IncidentActionIn | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    incident = _load_incident_for_mutation(db, user, incident_id)
    acknowledge_incident(db, incident, user, body.note if body else "")
    return get_incident(incident_id, db, user)


@router.post("/{incident_id}/resolve", response_model=schemas.IncidentDetailOut)
def resolve_incident_endpoint(
    incident_id: int,
    body: schemas.IncidentActionIn | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    incident = _load_incident_for_mutation(db, user, incident_id)
    resolve_incident(db, incident, user, body.note if body else "")
    return get_incident(incident_id, db, user)
