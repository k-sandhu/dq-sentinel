from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.audit import audit
from app.core.monitors import (
    ensure_monitor_pack,
    merge_monitor_pack_config,
    monitor_pack_out,
    normalize_monitor_pack_config,
    reconcile_monitor_pack,
)
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user, require_role

router = APIRouter(prefix="/datasets/{dataset_id}/monitor-pack", tags=["monitor-packs"])


def _get_dataset(db: Session, dataset_id: int) -> models.Dataset:
    ds = db.get(models.Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    return ds


@router.get("", response_model=schemas.MonitorPackOut)
def get_monitor_pack(
    dataset_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    ds = _get_dataset(db, dataset_id)
    pack = ensure_monitor_pack(db, ds)
    normalized = normalize_monitor_pack_config(pack.config)
    if pack.config != normalized:
        pack.config = normalized
    db.commit()
    db.refresh(pack)
    return monitor_pack_out(db, pack)


@router.patch("", response_model=schemas.MonitorPackOut)
def update_monitor_pack(
    dataset_id: int,
    body: schemas.MonitorPackUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    ds = _get_dataset(db, dataset_id)
    pack = ensure_monitor_pack(db, ds)
    before = {
        "enabled": pack.enabled,
        "config": normalize_monitor_pack_config(pack.config),
    }
    data = body.model_dump(exclude_unset=True)
    if "enabled" in data and data["enabled"] is not None:
        pack.enabled = data["enabled"]
    if "config" in data and data["config"] is not None:
        merged = merge_monitor_pack_config(normalize_monitor_pack_config(pack.config), data["config"])
        pack.config = normalize_monitor_pack_config(merged)
    pack.updated_at = utcnow()
    audit(
        db,
        user,
        "monitor_pack.update",
        "monitor_pack",
        pack.id,
        dataset_id=ds.id,
        before=before,
        after={"enabled": pack.enabled, "config": normalize_monitor_pack_config(pack.config)},
    )
    out = reconcile_monitor_pack(db, ds, actor_id=user.id)
    db.commit()
    return out


@router.post("/reconcile", response_model=schemas.MonitorPackOut)
def reconcile_now(
    dataset_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    ds = _get_dataset(db, dataset_id)
    pack = ensure_monitor_pack(db, ds)
    audit(
        db,
        user,
        "monitor_pack.reconcile",
        "monitor_pack",
        pack.id,
        dataset_id=ds.id,
    )
    out = reconcile_monitor_pack(db, ds, actor_id=user.id)
    db.commit()
    return out
