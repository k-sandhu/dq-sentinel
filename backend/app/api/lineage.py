"""Dataset DDL + table-level lineage endpoints (issue #51).

Declares full paths (no router prefix) because it spans /datasets and
/connections; mounted in app.main under /api/v1.

Every endpoint here introspects the SOURCE: table DDL, view SQL, the column
graph derived from them. That is a description of a customer's schema, so all of
it is gated on the per-connection grant model (#72) — a granted user must not be
able to read the shape of a source they were never granted, and a missing id must
be indistinguishable from an invisible one (both 404, per security.py).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.connectors.sa import connector_for
from app.core.lineage import build_lineage, column_subgraph, node_id_for, subgraph, table_key
from app.db import get_db
from app.security import assert_connection_visible, assert_dataset_visible, get_current_user

router = APIRouter(tags=["lineage"])


def _get_dataset(db: Session, dataset_id: int, user: models.User) -> models.Dataset:
    # Visible-or-404: the dataset must sit on a connection the caller can see (#72),
    # and a missing dataset returns the identical 404 body (see security.py).
    return assert_dataset_visible(db, user, dataset_id)


@router.get("/datasets/{dataset_id}/ddl", response_model=schemas.DatasetDdlOut)
def get_dataset_ddl(
    dataset_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    ds = _get_dataset(db, dataset_id, user)
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
    granularity: str = "table",
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    ds = _get_dataset(db, dataset_id, user)
    depth = max(1, min(5, depth))  # clamp rather than reject
    granularity = "column" if granularity == "column" else "table"
    try:
        graph = build_lineage(db, ds.connection, connector_for(ds.connection), granularity=granularity)
    except Exception as exc:  # noqa: BLE001 - surface introspection/source errors
        raise HTTPException(502, f"Could not build lineage: {exc}") from exc
    return subgraph(graph, node_id_for(ds.schema_name, ds.table_name), depth)


@router.get("/datasets/{dataset_id}/lineage/columns", response_model=schemas.LineageGraph)
def dataset_column_lineage(
    dataset_id: int,
    column: str,
    depth: int = 2,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    ds = _get_dataset(db, dataset_id, user)
    depth = max(1, min(8, depth))
    try:
        graph = build_lineage(db, ds.connection, connector_for(ds.connection), granularity="column")
    except Exception as exc:  # noqa: BLE001 - surface introspection/source errors
        raise HTTPException(502, f"Could not build lineage: {exc}") from exc
    return column_subgraph(graph, node_id_for(ds.schema_name, ds.table_name), column, depth)


@router.get("/connections/{connection_id}/lineage", response_model=schemas.LineageGraph)
def connection_lineage(
    connection_id: int,
    granularity: str = "table",
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    # Visible-or-404 (#72): identical response for a missing and an ungranted id.
    conn = assert_connection_visible(db, user, connection_id)
    granularity = "column" if granularity == "column" else "table"
    try:
        return build_lineage(db, conn, connector_for(conn), granularity=granularity)
    except Exception as exc:  # noqa: BLE001 - surface introspection/source errors
        raise HTTPException(502, f"Could not build lineage: {exc}") from exc
