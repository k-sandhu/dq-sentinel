"""Workbench endpoints: run guarded queries, browse schema/DDL, get suggested
investigation queries (LLM with heuristic fallback)."""

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.connectors.sa import Connector, connector_for
from app.connectors.safety import SqlNotAllowed
from app.core import suggest as heuristics
from app.core.profiler import jsonable, summarize_profile_for_llm
from app.db import get_db
from app.llm.client import llm_enabled
from app.security import assert_connection_role, assert_connection_visible, get_current_user

log = logging.getLogger(__name__)
router = APIRouter(tags=["workbench"])


def _connector(db: Session, connection_id: int) -> tuple[models.Connection, Connector]:
    conn = db.get(models.Connection, connection_id)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    return conn, connector_for(conn)


def execute_select(connector: Connector, sql: str, limit: int) -> schemas.QueryRunOut:
    """Run a guarded SELECT through a connector and shape the result.

    Shared by POST /query/run and POST /queries/{id}/run (saved_queries) so both
    go through the exact same execution + error-mapping path — no HTTP self-calls.
    """
    start = time.perf_counter()
    try:
        res = connector.run_select(sql, limit=limit)
    except SqlNotAllowed as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface driver errors to the analyst
        raise HTTPException(400, f"Query failed: {exc}") from exc
    elapsed = int((time.perf_counter() - start) * 1000)
    rows = [[jsonable(v) for v in row] for row in res.rows]
    return schemas.QueryRunOut(
        columns=res.columns,
        rows=rows,
        row_count=len(rows),
        truncated=len(rows) >= limit,
        elapsed_ms=elapsed,
    )


@router.post("/query/run", response_model=schemas.QueryRunOut)
def run_query(
    body: schemas.QueryRunIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    # Editor ON THIS connection: 404 if the connection isn't visible to the user,
    # 403 if visible but only granted viewer. Replaces the global editor gate so a
    # user can't run SQL against a connection they were never granted (#159).
    assert_connection_role(db, user, body.connection_id, "editor")
    _, connector = _connector(db, body.connection_id)
    return execute_select(connector, body.sql, body.limit)


@router.get("/connections/{connection_id}/schema", response_model=list[schemas.SchemaTable])
def get_schema(
    connection_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    assert_connection_visible(db, user, connection_id)
    _, connector = _connector(db, connection_id)
    try:
        return connector.schema_tree()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not introspect source: {exc}") from exc


@router.get("/connections/{connection_id}/ddl", response_model=schemas.DdlOut)
def get_ddl(
    connection_id: int,
    table: str,
    schema: str | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    assert_connection_visible(db, user, connection_id)
    _, connector = _connector(db, connection_id)
    known = {t["table_name"] for t in connector.list_tables()}
    if table not in known:
        raise HTTPException(404, f"Table {table!r} not found in this source")
    ddl, source = connector.get_ddl(table, schema)
    return schemas.DdlOut(table_name=table, ddl=ddl, source=source)


def _build_suggest_context(db: Session, body: schemas.SuggestIn, user: models.User):
    """Resolve connection/dataset/check/run/exception from whatever ids were given."""
    exception = check = run = dataset = None
    if body.exception_id:
        exception = db.get(models.ExceptionRecord, body.exception_id)
        if exception is None:
            raise HTTPException(404, "Exception not found")
        run = db.get(models.CheckRun, exception.run_id)
        check = db.get(models.Check, exception.check_id)
        dataset = db.get(models.Dataset, exception.dataset_id)
    elif body.run_id:
        run = db.get(models.CheckRun, body.run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        check = db.get(models.Check, run.check_id)
        dataset = db.get(models.Dataset, run.dataset_id)
    elif body.check_id:
        check = db.get(models.Check, body.check_id)
        if check is None:
            raise HTTPException(404, "Check not found")
        dataset = db.get(models.Dataset, check.dataset_id)
    elif body.dataset_id:
        dataset = db.get(models.Dataset, body.dataset_id)
        if dataset is None:
            raise HTTPException(404, "Dataset not found")

    if dataset is not None:
        connection = dataset.connection
    elif body.connection_id:
        connection = db.get(models.Connection, body.connection_id)
        if connection is None:
            raise HTTPException(404, "Connection not found")
    else:
        raise HTTPException(422, "Provide one of connection_id / dataset_id / check_id / run_id / exception_id")

    # Whatever id was supplied, the resolved connection must be visible to the
    # caller — otherwise this leaks suggestions/profile for ungranted sources (#159).
    assert_connection_visible(db, user, connection.id)

    profile = None
    if dataset is not None:
        p = (
            db.query(models.Profile)
            .filter(models.Profile.dataset_id == dataset.id)
            .order_by(models.Profile.id.desc())
            .first()
        )
        if p:
            profile = {
                "row_count": p.row_count,
                "sampled_rows": p.sampled_rows,
                "columns": p.columns,
                "table_facts": p.table_facts,
            }
    return connection, dataset, check, run, exception, profile


@router.post("/query/suggest", response_model=schemas.SuggestOut)
def suggest_queries(
    body: schemas.SuggestIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    connection, dataset, check, run, exception, profile = _build_suggest_context(db, body, user)
    connector = connector_for(connection)
    ref = (
        connector.table_ref(dataset.table_name, dataset.schema_name)
        if dataset
        else None
    )

    if llm_enabled() and ref:
        try:
            parts = [
                f"Dialect: {connector.kind}",
                f"Table: {ref}",
                "",
                "## Profile",
                summarize_profile_for_llm(profile) if profile else "(not profiled yet)",
            ]
            if check is not None:
                parts += [
                    "",
                    "## Failed check context",
                    f"check: {check.name} (type={check.check_type}, column={check.column_name}, "
                    f"params={check.params}, severity={check.severity})",
                ]
            if run is not None:
                parts.append(
                    f"run: status={run.status}, violations={run.violation_count}, metrics={run.metrics}"
                )
            if exception is not None:
                parts.append(
                    "exception row (truncated): "
                    + json.dumps(exception.row_data, default=str)[:600]
                    + f"\nreason: {exception.reason}"
                )
            if body.goal:
                parts += ["", f"## Analyst's goal\n{body.goal}"]
            parts += ["", "Propose the next investigation queries."]

            from app.llm.workbench import suggest_queries_llm

            suggestions = suggest_queries_llm("\n".join(parts))
            return schemas.SuggestOut(
                mode="llm", connection_id=connection.id, suggestions=suggestions
            )
        except Exception:  # noqa: BLE001 - heuristics must always answer
            log.exception("LLM suggestion failed; falling back to heuristics")

    if ref and check is not None:
        raw = heuristics.suggest_for_check(
            connector, ref, profile, check.check_type, check.column_name, check.params or {}
        )
    elif ref:
        raw = heuristics.suggest_for_dataset(connector, ref, profile)
    else:
        # connection-level: offer starter queries on the largest tables
        tables = connector.list_tables()[:5]
        raw = [
            {
                "title": f"Peek at {t['table_name']}",
                "sql": f"SELECT * FROM {connector.table_ref(t['table_name'], t['schema_name'])} LIMIT 50",
                "rationale": "Starter query — register the table as a dataset for tailored suggestions.",
            }
            for t in tables
        ]
    return schemas.SuggestOut(
        mode="heuristic", connection_id=connection.id, suggestions=heuristics.validated(raw)
    )
