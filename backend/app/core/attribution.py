"""Deterministic, PII-safe "why it failed" attribution for an exception (D6 / #176).

Good-vs-bad sample rows + a ranked column-attribution list, computed on read (no
LLM, no new tables). The FAILING sample is the stored ``ExceptionRecord.row_data``
for the check's cluster; the HEALTHY sample is a small bounded live query of PASSING
rows — the negation of the check's violation predicate (``healthy_where``). Both row
sets are redacted against the dataset's ``pii_columns``, and any attribution factor
on a redacted column is dropped, so a redacted column never leaks via a value OR a
label (two independent gates).
"""

from typing import Any

from sqlalchemy.orm import Session

from app import models, schemas
from app.connectors.sa import connector_for
from app.core.check_types import _lit
from app.llm.client import redact_rows
from app.models import utcnow

SAMPLE_ROWS = 10  # good_rows and bad_rows each capped here (a drawer, not an export)
MAX_FACTORS = 8  # ranked attribution list cap


def healthy_where(check: models.Check, col_sql: str) -> str | None:
    """The passing-rows predicate (the negation of the check's violation WHERE) for
    the column check types where it is well-defined; ``None`` => no per-row signal
    (unique / regex / freshness / volume / custom)."""
    p = check.params or {}
    t = check.check_type
    if t == "not_null":
        return f"{col_sql} IS NOT NULL"
    if t == "accepted_values":
        values = p.get("values") or []
        if not values:
            return None
        if p.get("case_sensitive", True):
            return f"{col_sql} IN ({', '.join(_lit(v) for v in values)})"
        lits = ", ".join(_lit(str(v).lower()) for v in values)
        return f"LOWER(CAST({col_sql} AS VARCHAR)) IN ({lits})"
    if t == "range":
        lo, hi = p.get("min"), p.get("max")
        if lo is None and hi is None:
            return None
        parts = [f"{col_sql} IS NOT NULL"]
        if lo is not None:
            parts.append(f"{col_sql} >= {_lit(lo)}")
        if hi is not None:
            parts.append(f"{col_sql} <= {_lit(hi)}")
        return " AND ".join(parts)
    if t == "string_length":
        lo, hi = p.get("min_len"), p.get("max_len")
        if lo is None and hi is None:
            return None
        parts = [f"{col_sql} IS NOT NULL"]
        if lo is not None:
            parts.append(f"LENGTH({col_sql}) >= {int(lo)}")
        if hi is not None:
            parts.append(f"LENGTH({col_sql}) <= {int(hi)}")
        return " AND ".join(parts)
    return None


def attribution_factors(
    bad_rows: list[dict[str, Any]],
    good_rows: list[dict[str, Any]],
    columns: list[str],
    pii_columns: list[str],
    max_factors: int = MAX_FACTORS,
) -> list[dict[str, Any]]:
    """Rank single-column value predicates by how cleanly they separate failing from
    healthy rows. Pure (no I/O). PII columns are excluded entirely, so no factor can
    surface a redacted value. A value common in failures but rare in healthy scores
    highest."""
    n_bad = len(bad_rows)
    if not n_bad:
        return []
    n_good = len(good_rows)
    pii = {c.lower() for c in pii_columns}
    factors: list[dict[str, Any]] = []
    for c in columns:
        if c.lower() in pii:
            continue
        fail_counts: dict[Any, int] = {}
        for r in bad_rows:
            v = r.get(c)
            fail_counts[v] = fail_counts.get(v, 0) + 1
        for value, fcount in fail_counts.items():
            fail_pct = fcount / n_bad
            hcount = sum(1 for r in good_rows if r.get(c) == value) if n_good else 0
            healthy_pct = (hcount / n_good) if n_good else 0.0
            score = fail_pct * 0.6 + max(fail_pct - healthy_pct, 0.0) * 0.4
            label = f"{c} = {value!r}" if isinstance(value, str) else f"{c} = {value}"
            factors.append(
                {
                    "column": c,
                    "label": label,
                    "pct": round(fail_pct * 100),
                    "fail_count": fcount,
                    "healthy_count": hcount,
                    "lift": round(fail_pct / (healthy_pct + 1e-9), 2),
                    "_score": score,
                }
            )
    factors.sort(key=lambda f: (-f["_score"], -f["pct"]))
    return [{k: v for k, v in f.items() if k != "_score"} for f in factors[:max_factors]]


def _pii_columns(dataset: models.Dataset | None) -> list[str]:
    k = dataset.knowledge if dataset else None
    return list(k.pii_columns) if k and k.pii_columns else []


def _failing_rows(db: Session, exc: models.ExceptionRecord) -> list[dict[str, Any]]:
    """Stored failing-row samples for this exception's (check, dataset) cluster."""
    recs = (
        db.query(models.ExceptionRecord)
        .filter(
            models.ExceptionRecord.check_id == exc.check_id,
            models.ExceptionRecord.dataset_id == exc.dataset_id,
        )
        .order_by(models.ExceptionRecord.id.desc())
        .limit(SAMPLE_ROWS)
        .all()
    )
    return [r.row_data for r in recs if isinstance(r.row_data, dict) and r.row_data]


def _columns_for(bad_rows: list[dict[str, Any]], check: models.Check | None) -> list[str]:
    cols: list[str] = []
    for r in bad_rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    primary = check.column_name if check else None
    if primary and primary in cols:  # the failing column reads first
        cols.remove(primary)
        cols.insert(0, primary)
    return cols


def _attr_rows(
    row_dicts: list[dict[str, Any]], columns: list[str], pii_columns: list[str]
) -> list[schemas.AttributionRow]:
    raw = [[r.get(c) for c in columns] for r in row_dicts]
    redacted = redact_rows(columns, raw, pii_columns)
    return [schemas.AttributionRow(columns=columns, cells=cells) for cells in redacted]


def _healthy_sample(
    dataset: models.Dataset, check: models.Check
) -> tuple[list[dict[str, Any]], str]:
    """(passing rows, reason). reason is '' on success, else a not-computable code."""
    connection = dataset.connection
    if not connection:
        return [], "source_unavailable"
    conn = connector_for(connection)
    where = healthy_where(check, conn.quote(check.column_name))
    if where is None:
        return [], "no_row_predicate"
    try:
        ref = conn.table_ref(dataset.table_name, dataset.schema_name)
        res = conn.run_select(f"SELECT * FROM {ref} WHERE {where}", limit=SAMPLE_ROWS)
        rows = [dict(zip(res.columns, r, strict=False)) for r in res.rows]
        return rows, ("no_healthy_rows" if not rows else "")
    except Exception:
        return [], "source_unavailable"


def build_attribution(
    db: Session, exc: models.ExceptionRecord
) -> schemas.ExceptionAttributionOut:
    check = db.get(models.Check, exc.check_id)
    dataset = db.get(models.Dataset, exc.dataset_id)
    pii = _pii_columns(dataset)

    def out(computable: bool, reason: str, **kw: Any) -> schemas.ExceptionAttributionOut:
        return schemas.ExceptionAttributionOut(
            exception_id=exc.id,
            computable=computable,
            reason=reason,
            columns=kw.get("columns", []),
            good_rows=kw.get("good", []),
            bad_rows=kw.get("bad", []),
            factors=kw.get("factors", []),
            summary=kw.get("summary", ""),
            generated_at=utcnow(),
        )

    bad_dicts = _failing_rows(db, exc)
    if not bad_dicts:
        return out(False, "no_failing_rows")

    columns = _columns_for(bad_dicts, check)
    bad_rows = _attr_rows(bad_dicts, columns, pii)
    pii_set = {c.lower() for c in pii}
    if not [c for c in columns if c.lower() not in pii_set]:
        return out(False, "all_columns_redacted", columns=columns, bad=bad_rows)
    if not check or not check.column_name or not dataset:
        return out(False, "no_row_predicate", columns=columns, bad=bad_rows)

    good_dicts, reason = _healthy_sample(dataset, check)
    if not good_dicts:
        # No baseline to contrast against -> show the failing rows, but never
        # fabricate separation factors without a healthy comparison.
        return out(False, reason or "no_healthy_rows", columns=columns, bad=bad_rows)

    factors = attribution_factors(bad_dicts, good_dicts, columns, pii)
    summary = ""
    if factors:
        top = factors[0]
        summary = f"{top['pct']}% of failing rows have {top['label']}"
    return out(
        True,
        "",
        columns=columns,
        good=_attr_rows(good_dicts, columns, pii),
        bad=bad_rows,
        factors=factors,
        summary=summary,
    )
