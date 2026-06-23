"""Insights API: curated app-metadata aggregates (issue #69).

The analyst's flagship ask — *"a matrix of the checks I care about (possibly from
different tables/databases), dates in the columns, ✓/✗ per day"* — is built from
**app metadata** (``check_runs`` history), not source-database data. There is no
sanctioned read path for analysts or LLM widgets over app metadata beyond the
fixed dashboard endpoint, and the wrong fix is letting anyone (human or LLM) run
SQL against the app DB: it holds ``users``, DSNs (#24), and chat history.

This module is that sanctioned surface: a small, parameterized, indexed,
**curated-not-raw** API. It is the ONLY app-metadata read surface for widgets and
the NL widget generator — any future "just let the LLM query the app DB"
suggestion should die against this comment.

Cost bounds ARE the caps: 25 checks x 30 days against the indexed
``ix_runs_check_started`` table is the designed envelope; the series window caps
at 90 days. Resist per-hour intervals or year windows until a need is shown.

All columns/buckets are **UTC days** (epic standard #5 — honest time); the schema
descriptions say so and the frontend labels honestly.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.exceptions_api import _common_filters, _filtered
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user, visible_connection_ids

router = APIRouter(prefix="/insights", tags=["insights"])

# Worst-of-day precedence: a day with 23 passes and 1 fail is a ✗ day — analysts
# ask "did anything go wrong", not "what was the average".
_STATUS_RANK = {"pass": 0, "warn": 1, "fail": 2, "error": 3}
_RANK_STATUS = {v: k for k, v in _STATUS_RANK.items()}


def _utc_day_columns(days: int) -> list[str]:
    """ISO date strings for the last ``days`` UTC days, oldest -> newest."""
    now = utcnow()
    return [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


@router.get("/check-matrix", response_model=schemas.CheckMatrixOut)
def check_matrix(
    check_ids: list[int] = Query(default_factory=list),
    days: int = 14,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Checks x UTC-days status grid. Each cell is the worst run status that day
    (precedence ``error > fail > warn > pass``); ``None`` = no run. Unknown or
    archived check ids are skipped silently so a dashboard survives a deleted check.

    Connection-grant scoping (#159): checks on connections the caller can't see are
    dropped from the result (same path as an unknown id), so a granted user's matrix
    never reveals other tenants' checks. Admin / zero-grant users see all.
    """
    if len(check_ids) > schemas.MAX_MATRIX_CHECKS:
        raise HTTPException(422, f"max {schemas.MAX_MATRIX_CHECKS} checks per matrix")
    if days not in schemas.MATRIX_DAYS:
        raise HTTPException(422, f"days must be one of {', '.join(map(str, schemas.MATRIX_DAYS))}")

    # De-duplicate, preserving the caller's order (the order they picked checks).
    ordered_ids: list[int] = list(dict.fromkeys(check_ids))
    columns = _utc_day_columns(days)
    if not ordered_ids:
        return schemas.CheckMatrixOut(columns=columns, rows=[])

    # Resolve names for EXISTING checks in one joined query (no N+1). Missing ids
    # simply don't appear in this map and are skipped below.
    vis = visible_connection_ids(db, user)  # None -> unrestricted (admin / zero-grant) (#159)
    meta_q = (
        db.query(
            models.Check.id,
            models.Check.name,
            models.Check.severity,
            models.Dataset.id,
            models.Dataset.display_name,
            models.Dataset.schema_name,
            models.Dataset.table_name,
            models.Connection.name,
        )
        .join(models.Dataset, models.Dataset.id == models.Check.dataset_id)
        .join(models.Connection, models.Connection.id == models.Dataset.connection_id)
        .filter(models.Check.id.in_(ordered_ids))
    )
    if vis is not None:
        meta_q = meta_q.filter(models.Dataset.connection_id.in_(vis))
    meta_rows = meta_q.all()
    meta = {r[0]: r for r in meta_rows}

    # Bucket runs by (check, UTC day): worst status + run count. One indexed query.
    cutoff = utcnow() - timedelta(days=days)
    runs = (
        db.query(models.CheckRun.check_id, models.CheckRun.started_at, models.CheckRun.status)
        .filter(models.CheckRun.check_id.in_(ordered_ids), models.CheckRun.started_at >= cutoff)
        .all()
    )
    column_index = {d: i for i, d in enumerate(columns)}
    # per check: list parallel to columns of [worst_rank | None, run_count]
    buckets: dict[int, list[list]] = {
        cid: [[None, 0] for _ in columns] for cid in ordered_ids
    }
    for cid, started_at, status in runs:
        day = started_at.strftime("%Y-%m-%d")
        idx = column_index.get(day)
        if idx is None:
            continue
        cell = buckets[cid][idx]
        cell[1] += 1
        rank = _STATUS_RANK.get(status)
        if rank is not None and (cell[0] is None or rank > cell[0]):
            cell[0] = rank

    rows: list[schemas.CheckMatrixRow] = []
    for cid in ordered_ids:
        m = meta.get(cid)
        if m is None:  # unknown/archived/deleted — skip silently
            continue
        _, check_name, severity, dataset_id, display_name, schema_name, table_name, conn_name = m
        dataset_name = display_name or (f"{schema_name}.{table_name}" if schema_name else table_name)
        cells = [
            schemas.CheckMatrixCell(
                status=_RANK_STATUS[rank] if rank is not None else None,
                runs=count,
            )
            for rank, count in buckets[cid]
        ]
        rows.append(
            schemas.CheckMatrixRow(
                check_id=cid,
                check_name=check_name,
                dataset_id=dataset_id,
                dataset_name=dataset_name,
                connection_name=conn_name,
                severity=severity,
                cells=cells,
            )
        )
    return schemas.CheckMatrixOut(columns=columns, rows=rows)


@router.get("/exception-series", response_model=schemas.ExceptionSeriesOut)
def exception_series(
    filters: dict = Depends(_common_filters),
    days: int = 30,
    interval: str = "day",
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Daily counts of NEW exceptions (by ``first_seen_at``) matching the same
    filter params as ``GET /exceptions`` — reuses that endpoint's ``_filtered``
    helper so filters never drift. ``interval`` is fixed to "day" in v1 (the
    param exists for forward compatibility)."""
    if interval != "day":
        raise HTTPException(422, 'interval must be "day"')
    if not 1 <= days <= schemas.MAX_SERIES_DAYS:
        raise HTTPException(422, f"days must be between 1 and {schemas.MAX_SERIES_DAYS}")

    columns = _utc_day_columns(days)
    cutoff = utcnow() - timedelta(days=days)
    query = _filtered(db, current_user=user, **filters).filter(
        models.ExceptionRecord.first_seen_at >= cutoff
    )
    counts: dict[str, int] = dict.fromkeys(columns, 0)
    for (first_seen_at,) in query.with_entities(models.ExceptionRecord.first_seen_at).all():
        day = first_seen_at.strftime("%Y-%m-%d")
        if day in counts:
            counts[day] += 1
    points = [schemas.SeriesPoint(t=d, value=counts[d]) for d in columns]
    return schemas.ExceptionSeriesOut(points=points, total=sum(counts.values()))
