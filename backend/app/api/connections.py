import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import connection_out
from app.connectors.dialects import REGISTRY, DriverNotInstalled, driver_installed
from app.connectors.sa import Connector, SqlNotAllowed, connector_for, dispose_connection, kind_from_dsn
from app.core.audit import audit
from app.core.deletion import cleanup_dataset_dependents
from app.db import get_db
from app.security import get_current_user, require_role

router = APIRouter(prefix="/connections", tags=["connections"])


@router.get("/health", response_model=list[schemas.ConnectionHealth])
def fleet_health(db: Session = Depends(get_db), _: models.User = Depends(get_current_user)):
    """Test every source concurrently — one glance across the whole fleet."""
    conns = db.query(models.Connection).order_by(models.Connection.name).all()

    def probe(conn: models.Connection) -> schemas.ConnectionHealth:
        start = time.perf_counter()
        try:
            ok, message, _count = connector_for(conn).test()
        except Exception as exc:  # noqa: BLE001
            ok, message = False, f"{type(exc).__name__}: {exc}"
        return schemas.ConnectionHealth(
            id=conn.id,
            name=conn.name,
            ok=ok,
            message=message,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )

    if not conns:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(conns))) as pool:
        return list(pool.map(probe, conns))


@router.get("", response_model=list[schemas.ConnectionOut])
def list_connections(db: Session = Depends(get_db), _: models.User = Depends(get_current_user)):
    out = []
    for conn in db.query(models.Connection).order_by(models.Connection.name).all():
        count = db.query(models.Dataset).filter(models.Dataset.connection_id == conn.id).count()
        out.append(connection_out(conn, count))
    return out


@router.post("", response_model=schemas.ConnectionOut, status_code=201)
def create_connection(
    body: schemas.ConnectionIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("admin")),
):
    try:
        kind = kind_from_dsn(body.dsn)
    except SqlNotAllowed as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - malformed URL
        raise HTTPException(400, f"Invalid DSN: {exc}") from exc
    if db.query(models.Connection).filter(models.Connection.name == body.name).first():
        raise HTTPException(409, "A connection with this name already exists")
    conn = models.Connection(name=body.name, kind=kind, dsn=body.dsn, created_by_id=user.id)
    db.add(conn)
    db.flush()  # assign conn.id for the audit row
    audit(db, user, "connection.create", "connection", conn.id, name=conn.name, kind=conn.kind)
    db.commit()
    db.refresh(conn)
    return connection_out(conn, 0)


@router.post("/test", response_model=schemas.ConnectionTestOut)
def test_dsn(body: schemas.ConnectionIn, _: models.User = Depends(require_role("admin"))):
    """Test a DSN before saving it."""
    try:
        connector = Connector(body.dsn)
    except DriverNotInstalled as exc:
        return schemas.ConnectionTestOut(ok=False, message=str(exc))  # verbatim, incl. pip hint
    except Exception as exc:  # noqa: BLE001
        return schemas.ConnectionTestOut(ok=False, message=f"Invalid DSN: {exc}")
    ok, message, table_count = connector.test()
    return schemas.ConnectionTestOut(ok=ok, message=message, table_count=table_count)


@router.get("/engines", response_model=list[schemas.EngineInfo])
def list_engines(_: models.User = Depends(get_current_user)):
    """Supported engine kinds + driver availability — for the new-connection form.

    Declared before the /{connection_id} routes so the literal path wins.
    """
    infos = [
        schemas.EngineInfo(
            kind=spec.kind,
            label=spec.label,
            dsn_example=spec.dsn_example,
            driver_installed=driver_installed(spec),
            install_extra=spec.install_extra,
            notes=spec.notes,
        )
        for spec in REGISTRY.values()
    ]
    return sorted(infos, key=lambda e: e.label)


@router.post("/{connection_id}/test", response_model=schemas.ConnectionTestOut)
def test_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    conn = db.get(models.Connection, connection_id)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    try:
        ok, message, table_count = connector_for(conn).test()
    except DriverNotInstalled as exc:
        return schemas.ConnectionTestOut(ok=False, message=str(exc))  # verbatim, incl. pip hint
    return schemas.ConnectionTestOut(ok=ok, message=message, table_count=table_count)


@router.get("/{connection_id}/tables", response_model=list[schemas.TableInfo])
def list_tables(
    connection_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    conn = db.get(models.Connection, connection_id)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    try:
        tables = connector_for(conn).list_tables()
    except Exception as exc:  # noqa: BLE001 - surface driver errors
        raise HTTPException(502, f"Could not introspect source: {exc}") from exc

    registered = {
        (d.schema_name, d.table_name): d.id
        for d in db.query(models.Dataset).filter(models.Dataset.connection_id == connection_id).all()
    }
    return [
        schemas.TableInfo(
            schema_name=t["schema_name"],
            table_name=t["table_name"],
            kind=t["kind"],
            registered_dataset_id=registered.get((t["schema_name"], t["table_name"])),
        )
        for t in tables
    ]


@router.delete("/{connection_id}", status_code=204)
def delete_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("admin")),
):
    conn = db.get(models.Connection, connection_id)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    audit(db, user, "connection.delete", "connection", conn.id, name=conn.name, kind=conn.kind)
    dataset_ids = [d.id for d in conn.datasets]
    for dataset_id in dataset_ids:
        cleanup_dataset_dependents(db, dataset_id)
    db.delete(conn)  # cascades to datasets/checks/profiles/knowledge via ORM relationships
    db.commit()
    dispose_connection(connection_id)
