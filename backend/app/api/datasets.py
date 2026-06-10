from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import dataset_out
from app.config import get_settings
from app.connectors.sa import connector_for
from app.core.profiler import jsonable, profile_dataset
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user, require_role

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=list[schemas.DatasetOut])
def list_datasets(
    connection_id: int | None = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    q = db.query(models.Dataset)
    if connection_id is not None:
        q = q.filter(models.Dataset.connection_id == connection_id)
    return [dataset_out(db, d) for d in q.order_by(models.Dataset.table_name).all()]


@router.post("/register", response_model=list[schemas.DatasetOut], status_code=201)
def register_datasets(
    body: schemas.DatasetRegisterIn,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("editor")),
):
    conn = db.get(models.Connection, body.connection_id)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    created: list[models.Dataset] = []
    for t in body.tables:
        exists = (
            db.query(models.Dataset)
            .filter(
                models.Dataset.connection_id == conn.id,
                models.Dataset.schema_name == t.schema_name,
                models.Dataset.table_name == t.table_name,
            )
            .first()
        )
        if exists:
            continue
        ds = models.Dataset(
            connection_id=conn.id,
            schema_name=t.schema_name,
            table_name=t.table_name,
            display_name=t.table_name,
        )
        db.add(ds)
        created.append(ds)
    db.commit()
    return [dataset_out(db, d) for d in created]


def _get_dataset(db: Session, dataset_id: int) -> models.Dataset:
    ds = db.get(models.Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    return ds


@router.get("/{dataset_id}", response_model=schemas.DatasetOut)
def get_dataset(
    dataset_id: int, db: Session = Depends(get_db), _: models.User = Depends(get_current_user)
):
    return dataset_out(db, _get_dataset(db, dataset_id))


@router.get("/{dataset_id}/columns", response_model=list[schemas.ColumnInfo])
def get_columns(
    dataset_id: int, db: Session = Depends(get_db), _: models.User = Depends(get_current_user)
):
    ds = _get_dataset(db, dataset_id)
    connector = connector_for(ds.connection)
    return connector.get_columns(ds.table_name, ds.schema_name)


@router.get("/{dataset_id}/preview", response_model=schemas.PreviewOut)
def preview(
    dataset_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    ds = _get_dataset(db, dataset_id)
    connector = connector_for(ds.connection)
    ref = connector.table_ref(ds.table_name, ds.schema_name)
    res = connector.run_select(f"SELECT * FROM {ref}", limit=min(limit, 200))
    rows = [[jsonable(v) for v in row] for row in res.rows]
    return schemas.PreviewOut(columns=res.columns, rows=rows, total_rows=ds.row_count)


@router.post("/{dataset_id}/profile", response_model=schemas.ProfileOut)
def run_profile(
    dataset_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("editor")),
):
    ds = _get_dataset(db, dataset_id)
    settings = get_settings()
    connector = connector_for(ds.connection)
    try:
        result = profile_dataset(
            connector, ds.table_name, ds.schema_name, sample_rows=settings.profile_sample_rows
        )
    except Exception as exc:  # noqa: BLE001 - surface profiling failures
        raise HTTPException(502, f"Profiling failed: {exc}") from exc

    profile = models.Profile(
        dataset_id=ds.id,
        row_count=result["row_count"],
        sampled_rows=result["sampled_rows"],
        columns=result["columns"],
        table_facts=result["table_facts"],
    )
    ds.row_count = result["row_count"]
    ds.last_profiled_at = utcnow()
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return _profile_out(profile)


def _profile_out(profile: models.Profile) -> schemas.ProfileOut:
    return schemas.ProfileOut(
        id=profile.id,
        dataset_id=profile.dataset_id,
        created_at=profile.created_at,
        row_count=profile.row_count,
        sampled_rows=profile.sampled_rows,
        columns=profile.columns,
        table_facts=profile.table_facts,
    )


@router.get("/{dataset_id}/profile", response_model=schemas.ProfileOut)
def latest_profile(
    dataset_id: int, db: Session = Depends(get_db), _: models.User = Depends(get_current_user)
):
    profile = (
        db.query(models.Profile)
        .filter(models.Profile.dataset_id == dataset_id)
        .order_by(models.Profile.id.desc())
        .first()
    )
    if profile is None:
        raise HTTPException(404, "Dataset has not been profiled yet")
    return _profile_out(profile)


@router.get("/{dataset_id}/exploration")
def get_exploration(
    dataset_id: int, db: Session = Depends(get_db), _: models.User = Depends(get_current_user)
):
    ds = _get_dataset(db, dataset_id)
    return ds.exploration or {"insights": [], "queries_run": 0}


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("admin")),
):
    ds = _get_dataset(db, dataset_id)
    # ORM cascades cover checks/profiles/knowledge; clean up the rest explicitly
    db.query(models.ExceptionRecord).filter(models.ExceptionRecord.dataset_id == dataset_id).delete()
    db.query(models.RcaSession).filter(models.RcaSession.dataset_id == dataset_id).delete()
    db.query(models.CheckRun).filter(models.CheckRun.dataset_id == dataset_id).delete()
    db.delete(ds)
    db.commit()
