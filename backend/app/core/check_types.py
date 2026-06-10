"""Check registry: every check type knows how to validate its params and execute
against a source via the read-only connector, returning violations + sample rows.

Adding a check type: add a CheckType entry here, document params, add a test in
tests/test_checks.py, and add a label in frontend/src/lib/checkMeta.ts.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.sa import Connector
from app.connectors.safety import guard_sql
from app.core import ml
from app.core.profiler import jsonable


@dataclass
class CheckContext:
    connector: Connector
    table: str
    schema: str | None
    column: str | None
    params: dict[str, Any]
    db: Session | None = None
    check_id: int | None = None

    @property
    def ref(self) -> str:
        return self.connector.table_ref(self.table, self.schema)

    @property
    def col(self) -> str:
        if not self.column:
            raise ValueError("This check type requires a column")
        return self.connector.quote(self.column)


@dataclass
class CheckResult:
    violation_count: int
    rows_evaluated: int | None = None
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # parallel to sample_rows
    scores: list[float | None] = field(default_factory=list)  # parallel to sample_rows
    metrics: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


@dataclass
class CheckType:
    key: str
    label: str
    description: str
    needs_column: bool
    params: list[dict[str, Any]]
    run: Callable[[CheckContext], CheckResult]


def _truncate_row(row: dict[str, Any], max_str: int = 200) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        v = jsonable(v)
        if isinstance(v, str) and len(v) > max_str:
            v = v[:max_str] + "…"
        out[k] = v
    return out


def _sample_where(ctx: CheckContext, where: str, params: dict[str, Any] | None = None) -> CheckResult:
    """Count + sample rows matching a violation WHERE clause."""
    settings = get_settings()
    count = int(ctx.connector.scalar(f"SELECT COUNT(*) FROM {ctx.ref} WHERE {where}", params) or 0)
    rows: list[dict[str, Any]] = []
    if count:
        res = ctx.connector.run_select(
            f"SELECT * FROM {ctx.ref} WHERE {where}", params, limit=settings.exception_sample_rows
        )
        rows = [_truncate_row(dict(zip(res.columns, r, strict=False))) for r in res.rows]
    total = int(ctx.connector.scalar(f"SELECT COUNT(*) FROM {ctx.ref}") or 0)
    return CheckResult(
        violation_count=count,
        rows_evaluated=total,
        sample_rows=rows,
        metrics={"row_count": total},
    )


def _lit(v: Any) -> str:
    """Inline a value as a safe SQL literal (numbers as-is, strings escaped)."""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


# ---------------------------------------------------------------- not_null
def _run_not_null(ctx: CheckContext) -> CheckResult:
    r = _sample_where(ctx, f"{ctx.col} IS NULL")
    r.reasons = [f"{ctx.column} is NULL"] * len(r.sample_rows)
    return r


# ---------------------------------------------------------------- unique
def _run_unique(ctx: CheckContext) -> CheckResult:
    settings = get_settings()
    dup_sql = (
        f"SELECT {ctx.col} AS v, COUNT(*) AS c FROM {ctx.ref} "
        f"WHERE {ctx.col} IS NOT NULL GROUP BY {ctx.col} HAVING COUNT(*) > 1"
    )
    agg = ctx.connector.run_select(
        f"SELECT COUNT(*) AS groups, COALESCE(SUM(c),0) AS dup_rows FROM ({dup_sql}) AS d"
    )
    groups, dup_rows = (int(agg.rows[0][0]), int(agg.rows[0][1])) if agg.rows else (0, 0)
    surplus = dup_rows - groups
    rows: list[dict[str, Any]] = []
    reasons: list[str] = []
    if groups:
        res = ctx.connector.run_select(
            f"SELECT * FROM {ctx.ref} WHERE {ctx.col} IN "
            f"(SELECT v FROM ({dup_sql}) AS d ORDER BY c DESC LIMIT 10)",
            limit=settings.exception_sample_rows,
        )
        rows = [_truncate_row(dict(zip(res.columns, r, strict=False))) for r in res.rows]
        reasons = [f"duplicate {ctx.column} = {row.get(ctx.column)!r}" for row in rows]
    total = int(ctx.connector.scalar(f"SELECT COUNT(*) FROM {ctx.ref}") or 0)
    return CheckResult(
        violation_count=surplus,
        rows_evaluated=total,
        sample_rows=rows,
        reasons=reasons,
        metrics={"duplicate_groups": groups, "duplicated_rows": dup_rows, "row_count": total},
        detail=f"{groups} value(s) appear more than once" if groups else "",
    )


# ---------------------------------------------------------------- accepted_values
def _run_accepted_values(ctx: CheckContext) -> CheckResult:
    values = ctx.params.get("values") or []
    if not values:
        raise ValueError("accepted_values requires non-empty 'values'")
    case_sensitive = ctx.params.get("case_sensitive", True)
    if case_sensitive:
        lits = ", ".join(_lit(v) for v in values)
        where = f"{ctx.col} IS NOT NULL AND {ctx.col} NOT IN ({lits})"
    else:
        lits = ", ".join(_lit(str(v).lower()) for v in values)
        where = f"{ctx.col} IS NOT NULL AND LOWER(CAST({ctx.col} AS VARCHAR)) NOT IN ({lits})"
    r = _sample_where(ctx, where)
    r.reasons = [f"{ctx.column} = {row.get(ctx.column)!r} not in accepted set" for row in r.sample_rows]
    return r


# ---------------------------------------------------------------- range
def _run_range(ctx: CheckContext) -> CheckResult:
    lo, hi = ctx.params.get("min"), ctx.params.get("max")
    if lo is None and hi is None:
        raise ValueError("range requires 'min' and/or 'max'")
    parts = []
    if lo is not None:
        parts.append(f"{ctx.col} < {_lit(lo)}")
    if hi is not None:
        parts.append(f"{ctx.col} > {_lit(hi)}")
    where = f"{ctx.col} IS NOT NULL AND (" + " OR ".join(parts) + ")"
    r = _sample_where(ctx, where)
    bounds = f"[{lo if lo is not None else '-inf'}, {hi if hi is not None else 'inf'}]"
    r.reasons = [f"{ctx.column} = {row.get(ctx.column)!r} outside {bounds}" for row in r.sample_rows]
    return r


# ---------------------------------------------------------------- string_length
def _run_string_length(ctx: CheckContext) -> CheckResult:
    lo, hi = ctx.params.get("min_len"), ctx.params.get("max_len")
    if lo is None and hi is None:
        raise ValueError("string_length requires 'min_len' and/or 'max_len'")
    parts = []
    if lo is not None:
        parts.append(f"LENGTH({ctx.col}) < {int(lo)}")
    if hi is not None:
        parts.append(f"LENGTH({ctx.col}) > {int(hi)}")
    where = f"{ctx.col} IS NOT NULL AND (" + " OR ".join(parts) + ")"
    r = _sample_where(ctx, where)
    r.reasons = [
        f"length of {ctx.column} = {len(str(row.get(ctx.column) or ''))} outside [{lo}, {hi}]"
        for row in r.sample_rows
    ]
    return r


# ---------------------------------------------------------------- regex_match
def _run_regex(ctx: CheckContext) -> CheckResult:
    pattern = ctx.params.get("pattern")
    if not pattern:
        raise ValueError("regex_match requires 'pattern'")
    re.compile(pattern)  # validate early
    settings = get_settings()

    if ctx.connector.kind == "postgresql":
        where = f"{ctx.col} IS NOT NULL AND CAST({ctx.col} AS TEXT) !~ :pat"
        r = _sample_where(ctx, where, {"pat": pattern})
    elif ctx.connector.kind == "duckdb":
        where = f"{ctx.col} IS NOT NULL AND NOT regexp_matches(CAST({ctx.col} AS VARCHAR), :pat)"
        r = _sample_where(ctx, where, {"pat": pattern})
    else:
        # SQLite has no native regex: scan a bounded sample in Python.
        max_scan = int(ctx.params.get("max_scan", 100_000))
        df = ctx.connector.fetch_df(f"SELECT * FROM {ctx.ref} WHERE {ctx.col} IS NOT NULL", limit=max_scan)
        rx = re.compile(pattern)
        col = ctx.column or ""
        mask = ~df[col].astype(str).str.match(rx, na=False)
        bad = df[mask]
        rows = [_truncate_row(rec) for rec in bad.head(settings.exception_sample_rows).to_dict("records")]
        r = CheckResult(
            violation_count=int(mask.sum()),
            rows_evaluated=len(df),
            sample_rows=rows,
            metrics={"scanned_rows": len(df), "engine": "python-fallback"},
        )
    r.reasons = [
        f"{ctx.column} = {row.get(ctx.column)!r} does not match /{pattern}/" for row in r.sample_rows
    ]
    return r


# ---------------------------------------------------------------- freshness
def _run_freshness(ctx: CheckContext) -> CheckResult:
    max_age = float(ctx.params.get("max_age_hours", 24))
    latest_raw = ctx.connector.scalar(f"SELECT MAX({ctx.col}) FROM {ctx.ref}")
    if latest_raw is None:
        return CheckResult(violation_count=1, detail="No values found for freshness column", metrics={})
    latest = pd.to_datetime(str(latest_raw), errors="coerce", utc=True)
    if latest is pd.NaT:
        return CheckResult(violation_count=1, detail=f"Cannot parse {latest_raw!r} as timestamp")
    age_h = (datetime.now(UTC) - latest.to_pydatetime()).total_seconds() / 3600
    stale = age_h > max_age
    return CheckResult(
        violation_count=1 if stale else 0,
        metrics={"latest": str(latest), "age_hours": round(age_h, 2), "max_age_hours": max_age},
        detail=(
            f"Newest {ctx.column} is {age_h:.1f}h old (SLA {max_age}h)" if stale else f"Fresh: {age_h:.1f}h old"
        ),
    )


# ---------------------------------------------------------------- row_count_min
def _run_row_count_min(ctx: CheckContext) -> CheckResult:
    min_rows = int(ctx.params.get("min_rows", 1))
    count = int(ctx.connector.scalar(f"SELECT COUNT(*) FROM {ctx.ref}") or 0)
    low = count < min_rows
    return CheckResult(
        violation_count=1 if low else 0,
        rows_evaluated=count,
        metrics={"row_count": count, "min_rows": min_rows},
        detail=f"Row count {count} below minimum {min_rows}" if low else f"Row count {count}",
    )


# ---------------------------------------------------------------- row_count_anomaly
def _run_row_count_anomaly(ctx: CheckContext) -> CheckResult:
    from app.models import CheckRun  # local import to avoid cycle

    sigma = float(ctx.params.get("sigma", 3.0))
    lookback = int(ctx.params.get("lookback_runs", 14))
    min_history = int(ctx.params.get("min_history", 5))
    count = int(ctx.connector.scalar(f"SELECT COUNT(*) FROM {ctx.ref}") or 0)
    metrics: dict[str, Any] = {"row_count": count}

    history: list[float] = []
    if ctx.db is not None and ctx.check_id is not None:
        runs = (
            ctx.db.query(CheckRun)
            .filter(CheckRun.check_id == ctx.check_id, CheckRun.status != "error")
            .order_by(CheckRun.started_at.desc())
            .limit(lookback)
            .all()
        )
        history = [float(r.metrics["row_count"]) for r in runs if r.metrics and "row_count" in r.metrics]

    if len(history) < min_history:
        metrics.update({"history_n": len(history), "note": "collecting baseline"})
        return CheckResult(0, rows_evaluated=count, metrics=metrics, detail="Building row-count baseline")

    mean = sum(history) / len(history)
    var = sum((h - mean) ** 2 for h in history) / len(history)
    std = max(var**0.5, max(1.0, mean * 0.005))  # floor: tolerate tiny jitter
    z = (count - mean) / std
    metrics.update({"history_n": len(history), "mean": round(mean, 1), "std": round(std, 2), "z": round(z, 2)})
    anomalous = abs(z) > sigma
    return CheckResult(
        violation_count=1 if anomalous else 0,
        rows_evaluated=count,
        metrics=metrics,
        detail=f"Row count {count} vs mean {mean:.0f} (z={z:+.1f}, σ-limit {sigma})",
    )


# ---------------------------------------------------------------- custom_sql
def _run_custom_sql(ctx: CheckContext) -> CheckResult:
    settings = get_settings()
    sql = guard_sql(ctx.params.get("sql") or "")
    count = int(ctx.connector.scalar(f"SELECT COUNT(*) FROM (\n{sql}\n) AS _v") or 0)
    rows: list[dict[str, Any]] = []
    if count:
        res = ctx.connector.run_select(sql, limit=settings.exception_sample_rows)
        rows = [_truncate_row(dict(zip(res.columns, r, strict=False))) for r in res.rows]
    return CheckResult(
        violation_count=count,
        sample_rows=rows,
        reasons=["row returned by custom violation query"] * len(rows),
        metrics={},
    )


# ---------------------------------------------------------------- ml_outlier
def _run_ml_outlier(ctx: CheckContext) -> CheckResult:
    settings = get_settings()
    columns = ctx.params.get("columns") or None
    contamination = float(ctx.params.get("contamination", 0.005))
    max_rows = int(ctx.params.get("max_rows", settings.ml_max_rows))

    df = ctx.connector.fetch_df(f"SELECT * FROM {ctx.ref}", limit=max_rows)
    result = ml.detect_outliers(df, columns=columns, contamination=contamination)

    keep = result.indices[: settings.exception_sample_rows]
    rows = [_truncate_row(rec) for rec in df.iloc[keep].to_dict("records")]
    scores = list(result.scores[: len(keep)])
    reasons = [
        f"multivariate outlier across [{', '.join(result.features)}] (score {s})" for s in scores
    ]
    return CheckResult(
        violation_count=len(result.indices),
        rows_evaluated=result.rows_scored,
        sample_rows=rows,
        reasons=reasons,
        scores=scores,
        metrics={
            "features": result.features,
            "contamination": contamination,
            "score_threshold": result.threshold,
            "rows_scored": result.rows_scored,
        },
        detail=f"{len(result.indices)} outliers across {len(result.features)} numeric features",
    )


def _p(name: str, type_: str, required: bool = False, default: Any = None, desc: str = "") -> dict:
    return {"name": name, "type": type_, "required": required, "default": default, "description": desc}


CHECK_TYPES: dict[str, CheckType] = {
    c.key: c
    for c in [
        CheckType("not_null", "Not null", "Column must not contain NULLs", True, [], _run_not_null),
        CheckType("unique", "Unique", "Column values must be unique", True, [], _run_unique),
        CheckType(
            "accepted_values", "Accepted values", "Values must belong to a fixed set", True,
            [_p("values", "list", True, [], "Allowed values"),
             _p("case_sensitive", "boolean", False, True, "Compare case-sensitively")],
            _run_accepted_values,
        ),
        CheckType(
            "range", "Range", "Numeric/date values must fall inside bounds", True,
            [_p("min", "number", False, None, "Lower bound"), _p("max", "number", False, None, "Upper bound")],
            _run_range,
        ),
        CheckType(
            "string_length", "String length", "String length must fall inside bounds", True,
            [_p("min_len", "number", False, None), _p("max_len", "number", False, None)],
            _run_string_length,
        ),
        CheckType(
            "regex_match", "Pattern match", "Values must match a regular expression", True,
            [_p("pattern", "string", True, None, "Python/POSIX regex"),
             _p("max_scan", "number", False, 100000, "Rows scanned on SQLite fallback")],
            _run_regex,
        ),
        CheckType(
            "freshness", "Freshness", "Newest timestamp must be recent", True,
            [_p("max_age_hours", "number", True, 24, "Maximum age of newest row")],
            _run_freshness,
        ),
        CheckType(
            "row_count_min", "Minimum row count", "Table must have at least N rows", False,
            [_p("min_rows", "number", True, 1)],
            _run_row_count_min,
        ),
        CheckType(
            "row_count_anomaly", "Row count anomaly", "Row count must track its recent history", False,
            [_p("sigma", "number", False, 3.0), _p("lookback_runs", "number", False, 14),
             _p("min_history", "number", False, 5)],
            _run_row_count_anomaly,
        ),
        CheckType(
            "custom_sql", "Custom SQL", "Query returning violating rows (read-only)", False,
            [_p("sql", "sql", True, None, "SELECT returning one row per violation")],
            _run_custom_sql,
        ),
        CheckType(
            "ml_outlier", "ML outlier (IsolationForest)", "Flag multivariate numeric outliers", False,
            [_p("columns", "list", False, None, "Numeric columns (default: auto-detect)"),
             _p("contamination", "number", False, 0.005, "Expected outlier fraction"),
             _p("max_rows", "number", False, None, "Sample size cap")],
            _run_ml_outlier,
        ),
    ]
}


def validate_check(check_type: str, column_name: str | None, params: dict[str, Any]) -> dict[str, Any]:
    """Validate type/column/params at creation time. Returns normalized params."""
    ct = CHECK_TYPES.get(check_type)
    if ct is None:
        raise ValueError(f"Unknown check type '{check_type}'. Known: {sorted(CHECK_TYPES)}")
    if ct.needs_column and not column_name:
        raise ValueError(f"Check type '{check_type}' requires a column")
    known = {p["name"] for p in ct.params}
    normalized = {k: v for k, v in (params or {}).items() if k in known or k == "tolerance"}
    for p in ct.params:
        if p["required"] and normalized.get(p["name"]) in (None, "", []):
            raise ValueError(f"Check type '{check_type}' requires param '{p['name']}'")
    if check_type == "custom_sql":
        guard_sql(normalized["sql"])
    if check_type == "regex_match":
        try:
            re.compile(normalized["pattern"])
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern: {exc}") from exc
    return normalized


def run_check_type(ctx: CheckContext, check_type: str) -> CheckResult:
    return CHECK_TYPES[check_type].run(ctx)
