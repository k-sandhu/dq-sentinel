from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import dataset_out
from app.config import get_settings
from app.connectors.sa import connector_for
from app.core import schema_monitor
from app.core.deletion import cleanup_dataset_dependents
from app.core.profiler import jsonable, profile_dataset
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user, require_role

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=list[schemas.DatasetOut])
def list_datasets(
    connection_id: int | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    query = db.query(models.Dataset)
    if connection_id is not None:
        query = query.filter(models.Dataset.connection_id == connection_id)
    if q:
        needle = f"%{q.lower()}%"
        query = query.filter(
            func.lower(models.Dataset.table_name).like(needle)
            | func.lower(models.Dataset.display_name).like(needle)
        )
    return [dataset_out(db, d) for d in query.order_by(models.Dataset.table_name).all()]


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
    # Schema-history snapshot (#101): deduped, seeds the timeline + a pinnable baseline.
    try:
        cols = schema_monitor.introspect_columns(connector, ds.table_name, ds.schema_name)
        schema_monitor.capture_schema_snapshot(db, ds.id, cols, source="profile")
    except Exception:  # noqa: BLE001 - schema capture must never fail profiling
        pass
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


@router.get("/{dataset_id}/schema-history", response_model=schemas.SchemaHistoryOut)
def schema_history(
    dataset_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    """Deduped schema snapshots (newest first) with a change summary vs the prior one (#101)."""
    _get_dataset(db, dataset_id)
    snaps = (
        db.query(models.SchemaSnapshot)
        .filter(models.SchemaSnapshot.dataset_id == dataset_id)
        .order_by(models.SchemaSnapshot.id.asc())
        .all()
    )
    pinned = next((s.id for s in reversed(snaps) if s.is_baseline), None)
    out: list[schemas.SchemaSnapshotOut] = []
    prev_cols: list[dict] | None = None
    for s in snaps:
        summary = None
        if prev_cols is not None:
            summary = schemas.SchemaChangeSummary(
                **schema_monitor.summarize_delta(schema_monitor.diff_schemas(prev_cols, s.columns))
            )
        out.append(
            schemas.SchemaSnapshotOut(
                id=s.id,
                dataset_id=s.dataset_id,
                captured_at=s.captured_at,
                source=s.source,
                is_baseline=s.is_baseline,
                fingerprint=s.fingerprint,
                columns=s.columns,
                change_summary=summary,
            )
        )
        prev_cols = s.columns
    out.reverse()  # newest first
    return schemas.SchemaHistoryOut(dataset_id=dataset_id, pinned_baseline_id=pinned, snapshots=out[:limit])


@router.post("/{dataset_id}/schema-baseline", response_model=schemas.SchemaSnapshotOut, status_code=201)
def pin_schema_baseline(
    dataset_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("editor")),
):
    """Pin the dataset's CURRENT schema as the baseline for ``baseline=pinned`` checks (#101)."""
    ds = _get_dataset(db, dataset_id)
    connector = connector_for(ds.connection)
    try:
        cols = schema_monitor.introspect_columns(connector, ds.table_name, ds.schema_name)
    except Exception as exc:  # noqa: BLE001 - surface introspection failures
        raise HTTPException(502, f"Could not read schema: {exc}") from exc
    snap = schema_monitor.pin_baseline(db, ds.id, cols)
    db.commit()
    db.refresh(snap)
    return schemas.SchemaSnapshotOut(
        id=snap.id,
        dataset_id=snap.dataset_id,
        captured_at=snap.captured_at,
        source=snap.source,
        is_baseline=snap.is_baseline,
        fingerprint=snap.fingerprint,
        columns=snap.columns,
    )


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
    cleanup_dataset_dependents(db, dataset_id)
    db.delete(ds)
    db.commit()
