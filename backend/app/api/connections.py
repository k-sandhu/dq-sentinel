from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import connection_out
from app.connectors.sa import Connector, SqlNotAllowed, connector_for, dispose_connection, kind_from_dsn
from app.db import get_db
from app.security import get_current_user, require_role

router = APIRouter(prefix="/connections", tags=["connections"])


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
    db.commit()
    db.refresh(conn)
    return connection_out(conn, 0)


@router.post("/test", response_model=schemas.ConnectionTestOut)
def test_dsn(body: schemas.ConnectionIn, _: models.User = Depends(require_role("admin"))):
    """Test a DSN before saving it."""
    try:
        connector = Connector(body.dsn)
    except Exception as exc:  # noqa: BLE001
        return schemas.ConnectionTestOut(ok=False, message=f"Invalid DSN: {exc}")
    ok, message, table_count = connector.test()
    return schemas.ConnectionTestOut(ok=ok, message=message, table_count=table_count)


@router.post("/{connection_id}/test", response_model=schemas.ConnectionTestOut)
def test_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    conn = db.get(models.Connection, connection_id)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    ok, message, table_count = connector_for(conn).test()
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
    _: models.User = Depends(require_role("admin")),
):
    conn = db.get(models.Connection, connection_id)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    db.delete(conn)  # cascades to datasets/checks/runs via ORM relationships
    db.commit()
    dispose_connection(connection_id)
