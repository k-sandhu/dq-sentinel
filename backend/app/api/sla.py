"""SLA definitions + reliability rollups (issue #102)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.core import sla as sla_core
from app.db import get_db
from app.security import get_current_user, require_role

router = APIRouter(prefix="/sla", tags=["sla"])


def _scope_label(db: Session, sla: models.SLADefinition) -> tuple[str, int | None]:
    if sla.scope == "check":
        chk = db.get(models.Check, sla.scope_id)
        if chk is None:
            return f"check {sla.scope_id} (deleted)", None
        return chk.name, chk.dataset_id
    ds = db.get(models.Dataset, sla.scope_id)
    if ds is None:
        return f"dataset {sla.scope_id} (deleted)", None
    return (ds.display_name or ds.table_name), ds.id


def _sla_out(db: Session, sla: models.SLADefinition) -> schemas.SLAOut:
    label, dataset_id = _scope_label(db, sla)
    latest = sla_core.latest_evaluation(db, sla.id)
    return schemas.SLAOut(
        id=sla.id,
        name=sla.name,
        scope=sla.scope,
        scope_id=sla.scope_id,
        target_type=sla.target_type,
        objective=sla.objective,
        window=sla.window,
        enabled=sla.enabled,
        created_at=sla.created_at,
        scope_label=label,
        dataset_id=dataset_id,
        latest=schemas.SLAEvaluationOut.model_validate(latest) if latest else None,
    )


def _validate_scope(db: Session, scope: str, scope_id: int) -> None:
    if scope == "dataset" and db.get(models.Dataset, scope_id) is None:
        raise HTTPException(404, "Dataset not found")
    if scope == "check" and db.get(models.Check, scope_id) is None:
        raise HTTPException(404, "Check not found")


@router.get("", response_model=list[schemas.SLAOut])
def list_slas(
    dataset_id: int | None = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    slas = db.query(models.SLADefinition).order_by(models.SLADefinition.id).all()
    out = [_sla_out(db, s) for s in slas]
    if dataset_id is not None:
        out = [o for o in out if o.dataset_id == dataset_id]
    return out


@router.post("", response_model=schemas.SLAOut, status_code=201)
def create_sla(
    body: schemas.SLAIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    _validate_scope(db, body.scope, body.scope_id)
    sla = sla_core.create_sla_definition(
        db, user,
        name=body.name,
        scope=body.scope,
        scope_id=body.scope_id,
        target_type=body.target_type,
        objective=body.objective,
        window=body.window,
        enabled=body.enabled,
    )
    db.commit()
    db.refresh(sla)
    return _sla_out(db, sla)


# NOTE: declared before /{sla_id} so "reliability" isn't matched as an id.
@router.get("/reliability", response_model=schemas.ReliabilityOut)
def reliability(db: Session = Depends(get_db), _: models.User = Depends(get_current_user)):
    rows = [_sla_out(db, s) for s in db.query(models.SLADefinition).order_by(models.SLADefinition.id).all()]
    breached = sum(1 for r in rows if r.latest and r.latest.breached)
    # worst first: breached SLAs, then by error budget consumed
    rows.sort(key=lambda r: (not (r.latest and r.latest.breached), -(r.latest.budget_consumed if r.latest else 0.0)))
    return schemas.ReliabilityOut(total=len(rows), breached=breached, slas=rows)


@router.get("/{sla_id}", response_model=schemas.SLADetailOut)
def get_sla(sla_id: int, db: Session = Depends(get_db), _: models.User = Depends(get_current_user)):
    sla = db.get(models.SLADefinition, sla_id)
    if sla is None:
        raise HTTPException(404, "SLA not found")
    base = _sla_out(db, sla)
    evals = (
        db.query(models.SLAEvaluation)
        .filter(models.SLAEvaluation.sla_id == sla_id)
        .order_by(models.SLAEvaluation.id.desc())
        .limit(60)
        .all()
    )
    evals.reverse()  # oldest -> newest for the burn-down chart
    return schemas.SLADetailOut(
        **base.model_dump(),
        evaluations=[schemas.SLAEvaluationOut.model_validate(e) for e in evals],
    )


@router.patch("/{sla_id}", response_model=schemas.SLAOut)
def update_sla(
    sla_id: int,
    body: schemas.SLAUpdate,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("editor")),
):
    sla = db.get(models.SLADefinition, sla_id)
    if sla is None:
        raise HTTPException(404, "SLA not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(sla, field, value)
    db.flush()
    sla_core.evaluate_sla(db, sla)
    db.commit()
    db.refresh(sla)
    return _sla_out(db, sla)


@router.post("/{sla_id}/evaluate", response_model=schemas.SLAOut)
def evaluate_now(
    sla_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("editor")),
):
    sla = db.get(models.SLADefinition, sla_id)
    if sla is None:
        raise HTTPException(404, "SLA not found")
    sla_core.evaluate_sla(db, sla)
    db.commit()
    return _sla_out(db, sla)


@router.delete("/{sla_id}", status_code=204)
def delete_sla(
    sla_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("editor")),
):
    sla = db.get(models.SLADefinition, sla_id)
    if sla is None:
        raise HTTPException(404, "SLA not found")
    db.delete(sla)  # cascade deletes evaluations
    db.commit()
