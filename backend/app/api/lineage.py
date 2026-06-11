"""Dataset DDL + table-level lineage endpoints (issue #51).

Declares full paths (no router prefix) because it spans /datasets and
/connections; mounted in app.main under /api/v1.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.connectors.sa import connector_for
from app.core.lineage import build_lineage, node_id_for, subgraph, table_key
from app.db import get_db
from app.security import get_current_user

router = APIRouter(tags=["lineage"])


def _get_dataset(db: Session, dataset_id: int) -> models.Dataset:
    ds = db.get(models.Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    return ds


@router.get("/datasets/{dataset_id}/ddl", response_model=schemas.DatasetDdlOut)
def get_dataset_ddl(
    dataset_id: int, db: Session = Depends(get_db), _: models.User = Depends(get_current_user)
):
    ds = _get_dataset(db, dataset_id)
    try:
        connector = connector_for(ds.connection)
        ddl, source = connector.get_ddl(ds.table_name, ds.schema_name)
        tables = connector.list_tables()
    except Exception as exc:  # noqa: BLE001 - surface driver errors
        raise HTTPException(502, f"Could not read definition from source: {exc}") from exc

    kind = "table"
    want = table_key(ds.schema_name, ds.table_name)
    for t in tables:
        if table_key(t.get("schema_name"), t["table_name"]) == want:
            kind = t.get("kind") or "table"
            break
    return schemas.DatasetDdlOut(dataset_id=ds.id, ddl=ddl, source=source, kind=kind)


@router.get("/datasets/{dataset_id}/lineage", response_model=schemas.LineageGraph)
def dataset_lineage(
    dataset_id: int,
    depth: int = 2,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    ds = _get_dataset(db, dataset_id)
    depth = max(1, min(5, depth))  # clamp rather than reject
    try:
        graph = build_lineage(db, ds.connection, connector_for(ds.connection))
    except Exception as exc:  # noqa: BLE001 - surface introspection/source errors
        raise HTTPException(502, f"Could not build lineage: {exc}") from exc
    return subgraph(graph, node_id_for(ds.schema_name, ds.table_name), depth)


@router.get("/connections/{connection_id}/lineage", response_model=schemas.LineageGraph)
def connection_lineage(
    connection_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    conn = db.get(models.Connection, connection_id)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    try:
        return build_lineage(db, conn, connector_for(conn))
    except Exception as exc:  # noqa: BLE001 - surface introspection/source errors
        raise HTTPException(502, f"Could not build lineage: {exc}") from exc
