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

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.dialects import nan_predicate_builder
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


def _bool_param(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _successful_runs(ctx: CheckContext, lookback: int) -> list[Any]:
    if ctx.db is None or ctx.check_id is None:
        return []
    from app.models import CheckRun  # local import to avoid cycle

    return (
        ctx.db.query(CheckRun)
        .filter(CheckRun.check_id == ctx.check_id, CheckRun.status != "error")
        .order_by(CheckRun.started_at.desc())
        .limit(lookback)
        .all()
    )


def _numeric_metric_history(ctx: CheckContext, metric: str, lookback: int) -> list[float]:
    values: list[float] = []
    for run in _successful_runs(ctx, lookback):
        if not isinstance(run.metrics, dict) or metric not in run.metrics:
            continue
        try:
            values.append(float(run.metrics[metric]))
        except (TypeError, ValueError):
            continue
    return values


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
    if not isinstance(values, list) or not values:
        raise ValueError("accepted_values requires a non-empty list 'values'")
    case_sensitive = ctx.params.get("case_sensitive", True)
    # Bind values as parameters rather than inlining literals: doubling quotes is
    # not enough on backslash-escaping dialects (MySQL/MariaDB), where an inlined
    # value ending in `\` breaks out of the string and past guard_sql. Binding is
    # dialect-safe for every engine.
    if case_sensitive:
        params = {f"v{i}": v for i, v in enumerate(values)}
        placeholders = ", ".join(f":v{i}" for i in range(len(values)))
        where = f"{ctx.col} IS NOT NULL AND {ctx.col} NOT IN ({placeholders})"
    else:
        params = {f"v{i}": str(v).lower() for i, v in enumerate(values)}
        placeholders = ", ".join(f":v{i}" for i in range(len(values)))
        where = f"{ctx.col} IS NOT NULL AND LOWER(CAST({ctx.col} AS VARCHAR)) NOT IN ({placeholders})"
    r = _sample_where(ctx, where, params)
    r.reasons = [f"{ctx.column} = {row.get(ctx.column)!r} not in accepted set" for row in r.sample_rows]
    return r


# ---------------------------------------------------------------- range
def _column_dtype(ctx: CheckContext) -> str | None:
    """The checked column's declared type, or None if it can't be resolved."""
    try:
        columns = ctx.connector.get_columns(ctx.table, ctx.schema)
    except Exception:  # noqa: BLE001 - introspection is optional here; see caller
        return None
    target = (ctx.column or "").lower()
    for col in columns:
        if str(col.get("name", "")).lower() == target:
            return str(col.get("dtype") or "")
    return None


def _nan_violation_predicate(ctx: CheckContext) -> str | None:
    """SQL that is TRUE for a NaN in the checked column, or None when NaN can't occur.

    NaN survives `col IS NOT NULL AND col < :min` on every engine that stores it, so
    a min-only range check silently passed NaN (#271). The spelling is per-dialect
    (``connectors/dialects.py`` explains why ``col != col`` is NOT it) and is applied
    only to column types that can actually hold a NaN — an ungated predicate is a
    binder/conversion error on date and integer columns, which would turn a working
    date-range check into a failing one. If either lookup comes up empty the check
    behaves exactly as before rather than erroring.
    """
    build = nan_predicate_builder(ctx.connector.kind)
    if build is None:  # engine cannot store NaN — don't even introspect the column
        return None
    dtype = _column_dtype(ctx)
    return build(ctx.col, dtype) if dtype else None


def _run_range(ctx: CheckContext) -> CheckResult:
    lo, hi = ctx.params.get("min"), ctx.params.get("max")
    if lo is None and hi is None:
        raise ValueError("range requires 'min' and/or 'max'")
    parts = []
    params: dict[str, Any] = {}
    if lo is not None:
        parts.append(f"{ctx.col} < :rmin")
        params["rmin"] = lo
    if hi is not None:
        parts.append(f"{ctx.col} > :rmax")
        params["rmax"] = hi
    # Unconditional: a max bound happens to catch NaN (it sorts above everything on
    # the engines that store it), but relying on that leaves min-only checks blind.
    nan_predicate = _nan_violation_predicate(ctx)
    if nan_predicate is not None:
        parts.append(nan_predicate)
    where = f"{ctx.col} IS NOT NULL AND (" + " OR ".join(parts) + ")"
    r = _sample_where(ctx, where, params)
    bounds = f"[{lo if lo is not None else '-inf'}, {hi if hi is not None else 'inf'}]"
    r.reasons = [_range_reason(ctx, row, bounds) for row in r.sample_rows]
    return r


def _range_reason(ctx: CheckContext, row: dict[str, Any], bounds: str) -> str:
    column = ctx.column or ""
    if column in row and row[column] is None:
        # SQL already excluded NULLs, so a None back in Python is a float with no JSON
        # representation: NaN (#271) or ±Infinity. "= None outside [0, inf]" told an
        # analyst nothing; name the actual problem.
        return f"{column} is not a finite number (NaN or ±Infinity), so it cannot be inside {bounds}"
    return f"{column} = {row.get(column)!r} outside {bounds}"


def _char_length_fn(kind: str) -> str:
    """SQL function returning CHARACTER count (not bytes) for the dialect.

    ``LENGTH`` counts bytes on MySQL/MariaDB, so a character-length bound would be
    silently wrong for multibyte data; use ``CHAR_LENGTH`` there. MSSQL spells it
    ``LEN``. Everywhere else ``LENGTH`` already returns characters.
    """
    if kind in ("mysql", "mariadb", "clickhouse"):
        return "CHAR_LENGTH"
    if kind == "mssql":
        return "LEN"
    return "LENGTH"


# ---------------------------------------------------------------- string_length
def _run_string_length(ctx: CheckContext) -> CheckResult:
    lo, hi = ctx.params.get("min_len"), ctx.params.get("max_len")
    if lo is None and hi is None:
        raise ValueError("string_length requires 'min_len' and/or 'max_len'")
    length = _char_length_fn(ctx.connector.kind)
    parts = []
    if lo is not None:
        parts.append(f"{length}({ctx.col}) < {int(lo)}")
    if hi is not None:
        parts.append(f"{length}({ctx.col}) > {int(hi)}")
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


# ---------------------------------------------------------------- schema_contract
def _nullable_value(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower().replace("_", " ")
        if v in {"true", "1", "yes", "y", "nullable", "null"}:
            return True
        if v in {"false", "0", "no", "n", "not null", "required"}:
            return False
    return bool(value)


def _contract_columns(raw_columns: list[Any]) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_columns):
        if isinstance(raw, str):
            name = raw
            dtype = None
            nullable = None
        elif isinstance(raw, dict):
            name = raw.get("name") or raw.get("column") or raw.get("column_name")
            dtype = raw.get("dtype", raw.get("type", raw.get("data_type")))
            nullable = _nullable_value(raw.get("nullable"))
        else:
            raise ValueError("schema_contract expected_columns entries must be strings or objects")
        if not name:
            raise ValueError("schema_contract expected_columns entries require a column name")
        col: dict[str, Any] = {"name": str(name), "ordinal": i}
        if dtype is not None:
            col["dtype"] = str(dtype)
        if nullable is not None:
            col["nullable"] = nullable
        columns.append(col)
    return columns


def _contract_key(name: str, case_sensitive: bool) -> str:
    return name if case_sensitive else name.lower()


def _contract_type(dtype: Any) -> str | None:
    if dtype is None:
        return None
    return " ".join(str(dtype).strip().lower().split())


def _contract_public_col(col: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"name": col["name"]}
    if "dtype" in col:
        out["dtype"] = col["dtype"]
    if "nullable" in col:
        out["nullable"] = col["nullable"]
    return out


def _contract_diff_summary(metrics: dict[str, Any], count_added: bool) -> str:
    counts = [
        (len(metrics["missing"]), "missing"),
        (len(metrics["type_changed"]), "type change"),
        (len(metrics["nullability_changed"]), "nullability change"),
    ]
    if count_added:
        counts.append((len(metrics["added"]), "added"))
    parts = [f"{n} {label}{'' if n == 1 else 's'}" for n, label in counts if n]
    return ", ".join(parts)


def _run_schema_contract(ctx: CheckContext) -> CheckResult:
    expected_raw = ctx.params.get("expected_columns") or []
    expected = _contract_columns(expected_raw)
    if not expected:
        raise ValueError("schema_contract requires non-empty 'expected_columns'")

    current = _contract_columns(ctx.connector.get_columns(ctx.table, ctx.schema))
    allow_additive = _bool_param(ctx.params.get("allow_additive"), True)
    case_sensitive = _bool_param(ctx.params.get("case_sensitive"), False)

    expected_by_key = {_contract_key(c["name"], case_sensitive): c for c in expected}
    current_by_key = {_contract_key(c["name"], case_sensitive): c for c in current}

    missing = [
        _contract_public_col(col)
        for key, col in expected_by_key.items()
        if key not in current_by_key
    ]
    added = [
        _contract_public_col(col)
        for key, col in current_by_key.items()
        if key not in expected_by_key
    ]
    type_changed: list[dict[str, Any]] = []
    nullability_changed: list[dict[str, Any]] = []
    for key, expected_col in expected_by_key.items():
        current_col = current_by_key.get(key)
        if current_col is None:
            continue
        expected_type = _contract_type(expected_col.get("dtype"))
        current_type = _contract_type(current_col.get("dtype"))
        if expected_type is not None and current_type is not None and expected_type != current_type:
            type_changed.append(
                {
                    "column": expected_col["name"],
                    "expected": expected_col.get("dtype"),
                    "actual": current_col.get("dtype"),
                }
            )
        if "nullable" in expected_col and "nullable" in current_col:
            expected_nullable = bool(expected_col["nullable"])
            current_nullable = bool(current_col["nullable"])
            if expected_nullable != current_nullable:
                nullability_changed.append(
                    {
                        "column": expected_col["name"],
                        "expected": expected_nullable,
                        "actual": current_nullable,
                    }
                )

    violation_count = len(missing) + len(type_changed) + len(nullability_changed)
    if not allow_additive:
        violation_count += len(added)
    metrics = {
        "expected_count": len(expected),
        "current_count": len(current),
        "allow_additive": allow_additive,
        "case_sensitive": case_sensitive,
        "missing": missing,
        "added": added,
        "type_changed": type_changed,
        "nullability_changed": nullability_changed,
    }
    if violation_count:
        detail = f"Schema contract drift: {_contract_diff_summary(metrics, not allow_additive)}"
    elif added:
        detail = f"Schema contract matches; {len(added)} additive column(s) allowed"
    else:
        detail = "Schema contract matches"
    return CheckResult(
        violation_count=violation_count,
        rows_evaluated=None,
        metrics=metrics,
        detail=detail,
    )


# ---------------------------------------------------------------- freshness
def _parse_timestamp(value: Any) -> datetime | None:
    ts = pd.to_datetime(str(value), errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return ts.to_pydatetime()


def _freshness_adaptive_threshold(ctx: CheckContext, fallback_max_age: float) -> tuple[float, dict[str, Any]]:
    lookback = int(ctx.params.get("lookback_runs", 14))
    min_history = int(ctx.params.get("min_history", 3))
    multiplier = float(ctx.params.get("multiplier", 2.0))
    grace_hours = float(ctx.params.get("grace_hours", 0.0))

    history: list[datetime] = []
    for run in reversed(_successful_runs(ctx, lookback)):
        if not isinstance(run.metrics, dict):
            continue
        latest = _parse_timestamp(run.metrics.get("latest"))
        if latest is not None:
            history.append(latest)

    intervals = [
        (newer - older).total_seconds() / 3600
        for older, newer in zip(history, history[1:], strict=False)
        if newer > older
    ]
    metrics: dict[str, Any] = {
        "strategy": "adaptive",
        "default_max_age_hours": fallback_max_age,
        "history_n": len(history),
        "intervals_n": len(intervals),
        "lookback_runs": lookback,
        "min_history": min_history,
        "multiplier": multiplier,
        "grace_hours": grace_hours,
    }

    required_intervals = max(1, min_history - 1)
    if len(history) < min_history or len(intervals) < required_intervals:
        metrics.update(
            {
                "threshold_source": "default",
                "note": "insufficient freshness history; using configured default",
            }
        )
        return fallback_max_age, metrics

    observed_interval = float(np.median(intervals))
    max_age = max(0.0, observed_interval * multiplier + grace_hours)
    metrics.update(
        {
            "threshold_source": "history",
            "observed_interval_hours": round(observed_interval, 2),
        }
    )
    return max_age, metrics


def _run_freshness(ctx: CheckContext) -> CheckResult:
    """Newest timestamp must be recent. Future-dated rows (themselves a DQ smell)
    are excluded from the staleness computation and reported as a metric, so a
    handful of bad future dates can't mask a stale table.
    """
    now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    if ctx.connector.kind == "sqlite":
        not_future = f"datetime({ctx.col}) <= datetime(:now_iso)"
        is_future = f"datetime({ctx.col}) > datetime(:now_iso)"
    else:
        not_future = f"{ctx.col} <= CAST(:now_iso AS TIMESTAMP)"
        is_future = f"{ctx.col} > CAST(:now_iso AS TIMESTAMP)"
    params = {"now_iso": now_iso}

    latest_raw = ctx.connector.scalar(
        f"SELECT MAX({ctx.col}) FROM {ctx.ref} WHERE {not_future}", params
    )
    future_rows = int(
        ctx.connector.scalar(f"SELECT COUNT(*) FROM {ctx.ref} WHERE {is_future}", params) or 0
    )
    if latest_raw is None:
        return CheckResult(
            violation_count=1,
            detail="No non-future values found for freshness column",
            metrics={"future_rows": future_rows},
        )
    latest_dt = _parse_timestamp(latest_raw)
    if latest_dt is None:
        return CheckResult(violation_count=1, detail=f"Cannot parse {latest_raw!r} as timestamp")
    age_h = (datetime.now(UTC) - latest_dt).total_seconds() / 3600
    strategy = str(ctx.params.get("strategy") or "static").lower()
    if strategy == "adaptive":
        fallback = float(ctx.params.get("default_max_age_hours", ctx.params.get("max_age_hours", 24)))
        max_age, threshold_metrics = _freshness_adaptive_threshold(ctx, fallback)
    else:
        max_age = float(ctx.params.get("max_age_hours", 24))
        threshold_metrics = {}
    stale = age_h > max_age
    latest = str(pd.Timestamp(latest_dt))
    metrics = {
        "latest": latest,
        "age_hours": round(age_h, 2),
        "max_age_hours": round(max_age, 2) if strategy == "adaptive" else max_age,
        "future_rows": future_rows,
    }
    metrics.update(threshold_metrics)
    if strategy == "adaptive":
        source = metrics.get("threshold_source")
        if source == "history":
            threshold_detail = (
                f"adaptive max {max_age:.1f}h from median interval "
                f"{metrics.get('observed_interval_hours')}h"
            )
        else:
            threshold_detail = f"default max {max_age:.1f}h"
    else:
        threshold_detail = f"SLA {max_age}h"
    return CheckResult(
        violation_count=1 if stale else 0,
        metrics=metrics,
        detail=(
            f"Newest {ctx.column} is {age_h:.1f}h old ({threshold_detail})"
            if stale
            else f"Fresh: {age_h:.1f}h old"
        )
        + (f"; {future_rows} future-dated rows excluded" if future_rows else ""),
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
def _run_row_count_adaptive(ctx: CheckContext, count: int) -> CheckResult:
    lookback = int(ctx.params.get("lookback_runs", 14))
    min_history = int(ctx.params.get("min_history", 5))
    multiplier = float(ctx.params.get("multiplier", 3.5))
    history = _numeric_metric_history(ctx, "row_count", lookback)
    metrics: dict[str, Any] = {
        "row_count": count,
        "strategy": "adaptive",
        "history_n": len(history),
        "lookback_runs": lookback,
        "min_history": min_history,
        "multiplier": multiplier,
        "baseline_method": "median_mad",
    }

    if len(history) < min_history:
        metrics["note"] = "collecting adaptive baseline"
        return CheckResult(
            0,
            rows_evaluated=count,
            metrics=metrics,
            detail="Building adaptive row-count baseline",
        )

    center = float(np.median(history))
    abs_dev = [abs(h - center) for h in history]
    mad = float(np.median(abs_dev))
    floor = max(1.0, abs(center) * 0.005)
    robust_sigma = max(mad * 1.4826, floor)
    lower = max(0.0, center - multiplier * robust_sigma)
    upper = center + multiplier * robust_sigma
    score = (count - center) / robust_sigma
    anomalous = count < lower or count > upper
    metrics.update(
        {
            "baseline_center": round(center, 2),
            "mad": round(mad, 2),
            "robust_sigma": round(robust_sigma, 2),
            "lower_bound": round(lower, 2),
            "upper_bound": round(upper, 2),
            "score": round(score, 2),
        }
    )
    return CheckResult(
        violation_count=1 if anomalous else 0,
        rows_evaluated=count,
        metrics=metrics,
        detail=(
            f"Row count {count} vs adaptive median {center:.0f} "
            f"(bounds {lower:.0f}-{upper:.0f}, score={score:+.1f})"
        ),
    )


def _run_row_count_anomaly(ctx: CheckContext) -> CheckResult:
    strategy = str(ctx.params.get("strategy") or "sigma").lower()
    sigma = float(ctx.params.get("sigma", 3.0))
    lookback = int(ctx.params.get("lookback_runs", 14))
    min_history = int(ctx.params.get("min_history", 5))
    count = int(ctx.connector.scalar(f"SELECT COUNT(*) FROM {ctx.ref}") or 0)
    if strategy in ("adaptive", "robust"):
        return _run_row_count_adaptive(ctx, count)

    metrics: dict[str, Any] = {"row_count": count}
    history = _numeric_metric_history(ctx, "row_count", lookback)

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
    """Rows returned by an analyst-authored violation query.

    ``rows_evaluated`` is the dataset's row count, exactly as ``_sample_where`` reports
    it for not_null/range. It is not decoration: the runner only auto-resolves lingering
    open exceptions on a passing run when ``rows_evaluated is not None`` (runner.py), so
    leaving it at the default None put custom_sql in the same bucket as freshness /
    schema_contract / schema_change — checks that deliberately never auto-resolve. Those
    are alerts about a table-level *event*; custom_sql is genuinely row-level and records
    real ExceptionRecords, so once one was fixed and went green its old exceptions stayed
    `open` forever and every open-exception rollup overstated (#268).
    """
    settings = get_settings()
    sql = guard_sql(ctx.params.get("sql") or "")
    count = int(ctx.connector.scalar(f"SELECT COUNT(*) FROM (\n{sql}\n) AS _v") or 0)
    rows: list[dict[str, Any]] = []
    if count:
        res = ctx.connector.run_select(sql, limit=settings.exception_sample_rows)
        rows = [_truncate_row(dict(zip(res.columns, r, strict=False))) for r in res.rows]
    total = int(ctx.connector.scalar(f"SELECT COUNT(*) FROM {ctx.ref}") or 0)
    return CheckResult(
        violation_count=count,
        rows_evaluated=total,
        sample_rows=rows,
        reasons=["row returned by custom violation query"] * len(rows),
        metrics={"row_count": total},
    )


# ---------------------------------------------------------------- ml_outlier
# One genuine measure is enough. "Multivariate" describes what IsolationForest CAN
# do, not a precondition: it fits and scores a single column fine, and univariate
# detection is exactly what catches a 100x amount typo. Requiring two dimensions
# made the check silently no-op on ordinary narrow tables — the shipped `payments`
# sample has one real measure (`amount`) next to two surrogate keys, so after the
# #263 identifier filtering it returned 0 outliers and the e2e smoke test caught it.
_ML_MIN_FEATURES = 1
_ML_ID_DISTINCT_PCT = 0.98  # distinct/rows above which an integer column is a surrogate key


def _ml_feature_columns(profile: Any, available: set[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Pick IsolationForest features out of the dataset's newest profile.

    The check reads ``SELECT *`` so exception rows stay readable, which means the frame
    also carries primary keys, foreign keys, zone/vendor codes and timestamps. Those are
    numeric (or numeric-coercible) but have no magnitude, so the forest scores "rare id"
    and "recent row" as anomalies (#263). The profile already knows enough to drop them:
    ``kind`` separates temporal/string columns, ``pk_candidates`` names the surrogate
    keys, ``distinct_pct`` catches unnamed ones, and the column name catches codes.

    Both cardinality-based rules require an *integral* dtype, and that is load-bearing:
    ``pk_candidates`` means only "no nulls and distinct == row_count", which a continuous
    float measurement — a sensor reading, a price, a latency — satisfies routinely.
    Excluding those left fewer than two features and made the check pass with a note,
    i.e. ml_outlier silently stopped working on exactly the columns it exists for.
    ``pk_candidates`` is kept alongside ``distinct_pct`` rather than folded into it
    because it is the one signal that survives a profile row missing ``distinct_pct``.

    Bounds of the heuristic: a measurement named like a code (``error_code_seconds``) is
    dropped, a code with a plain name and low cardinality (``priority``) is kept, and a
    column added since the last profile is not a feature until the dataset is
    re-profiled. All three are recoverable — the check's ``columns`` param overrides
    everything here — and the dropped columns plus reasons land in the run's metrics.
    """
    facts = profile.table_facts or {}
    pk_candidates = {str(c).lower() for c in facts.get("pk_candidates", [])}
    keep: list[str] = []
    excluded: list[dict[str, str]] = []
    for col in profile.columns or []:
        name = col.get("name")
        if not name or name not in available:
            continue
        kind = col.get("kind")
        dtype = str(col.get("dtype") or "").lower()
        distinct_pct = _finite_float(col.get("distinct_pct")) or 0.0
        if kind != "numeric":
            reason = f"not a numeric column (kind={kind})"
        elif str(name).lower() in pk_candidates and "int" in dtype:
            reason = "primary-key candidate"
        elif ml.looks_like_identifier_name(str(name)):
            reason = "identifier/code-like name"
        elif distinct_pct >= _ML_ID_DISTINCT_PCT and "int" in dtype:
            reason = f"near-unique integer ({distinct_pct:.0%} distinct) — surrogate key"
        else:
            keep.append(name)
            continue
        excluded.append({"column": str(name), "reason": reason})
    return keep, excluded


def _run_ml_outlier(ctx: CheckContext) -> CheckResult:
    settings = get_settings()
    explicit = ctx.params.get("columns") or None
    contamination = float(ctx.params.get("contamination", 0.005))
    max_rows = int(ctx.params.get("max_rows", settings.ml_max_rows))

    df = ctx.connector.fetch_df(f"SELECT * FROM {ctx.ref}", limit=max_rows)

    excluded: list[dict[str, str]] = []
    profile = None if explicit else _latest_baseline_profile(ctx)
    if explicit:
        columns, feature_source = list(explicit), "params"
    elif profile is not None:
        columns, excluded = _ml_feature_columns(profile, set(df.columns))
        feature_source = "profile"
        if len(columns) < _ML_MIN_FEATURES:
            return CheckResult(
                violation_count=0,
                rows_evaluated=len(df),
                metrics={
                    "features": columns,
                    "feature_source": feature_source,
                    "excluded_features": excluded,
                    "contamination": contamination,
                    "rows_scored": 0,
                    "note": "no usable numeric features",
                },
                detail=(
                    f"skipped: only {len(columns)} usable numeric feature(s) after excluding "
                    f"{len(excluded)} identifier/non-numeric column(s) — set the check's "
                    "'columns' param to choose features explicitly"
                ),
            )
    else:
        # No profile to reason from: ml.detect_outliers' own auto-select applies.
        columns, feature_source = None, "auto"

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
            "feature_source": feature_source,
            "excluded_features": excluded,
            "contamination": contamination,
            "score_threshold": result.threshold,
            "rows_scored": result.rows_scored,
        },
        detail=f"{len(result.indices)} outliers across {len(result.features)} numeric features",
    )


# ---------------------------------------------------------------- distribution_drift
_PSI_EPS = 1e-4  # epsilon-clamp for empty bins so ln(a/e) stays finite
_DRIFT_SAMPLE_CAP = 2000  # reservoir sample size persisted for the KS path
# A numeric column is compared value-by-value instead of by quantile bins when the
# profile's exact top-value counts already describe essentially ALL of it (#265/#270):
# quantile bins cannot resolve point masses, and a coded / flag / zone / surcharge
# column *is* its value mix. Coverage, not cardinality, is the test — a 250-distinct
# zone column whose top 10 zones carry 99% of the rows is a value mix; a 250-distinct
# amount column whose top 10 values carry 3% of the rows is a continuous distribution.
# The distinct ceiling bounds the other end: past it the tail carries structure that 10
# stored values cannot represent, so the quantile path (which resolves the tail) wins.
#
# The coverage bar has to be near-total because the value path's weakness is a hard
# blind spot, not a soft one: everything outside the stored top 10 collapses into a
# single __other__ bucket, so a shift whose source AND destination both sit in that tail
# leaves all 11 bins bit-identical and scores PSI exactly 0.0 at any magnitude. Such a
# shift is not "seen but not localised" — it is not seen at all. At 0.5 that made half a
# column undetectable; at 0.95 the tail a shift can hide in is at most 5% of the rows.
_VALUE_MIX_MIN_COVERAGE = 0.95
_VALUE_MIX_MAX_DISTINCT = 1000
# Cap on the distinct values we are willing to re-count in SQL (_observed_value_counts).
_EXACT_COUNT_MAX_DISTINCT = 5000


def _latest_baseline_profile(ctx: CheckContext) -> Any | None:
    """Newest Profile row for this check's dataset (resolved via the check)."""
    if ctx.db is None or ctx.check_id is None:
        return None
    from app.models import Check, Profile  # local import to avoid a cycle

    check = ctx.db.get(Check, ctx.check_id)
    if check is None:
        return None
    return (
        ctx.db.query(Profile)
        .filter(Profile.dataset_id == check.dataset_id)
        .order_by(Profile.id.desc())
        .first()
    )


def _baseline_column(profile: Any, column: str) -> dict[str, Any] | None:
    for c in profile.columns or []:
        if c.get("name") == column:
            return c
    return None


def _psi(expected: np.ndarray, actual: np.ndarray) -> float:
    """Population Stability Index between two probability vectors (same length).
    Empty bins are epsilon-clamped so the log ratio stays finite."""
    e = np.clip(expected.astype(float), _PSI_EPS, None)
    a = np.clip(actual.astype(float), _PSI_EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def _finite_float(v: Any) -> float | None:
    """float(v) for anything numeric-looking, or None for NULL/NaN/inf/non-numeric."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _quantile_bins(bcol: dict[str, Any]) -> tuple[np.ndarray, np.ndarray] | None:
    """Histogram edges + each bin's baseline probability, from a profile column's
    stored quantiles. Returns ``(edges, expected)`` with ``expected`` summing to 1,
    or None when there is no usable distribution.

    The profiler stores p1/p5/p25/p50/p75/p95/p99. Those *are* exact quantiles of the
    baseline sample, so using their values as the bin edges makes each bin's baseline
    mass exactly the gap between the two surrounding probabilities — nothing is
    interpolated, and identical data reproduces `expected` exactly.

    That is the fix for #265: the previous scheme interpolated 10 equiprobable deciles
    out of those 7 points and then asserted 0.1 of the mass per bin. On a lumpy column
    (integers, codes, point masses) the interpolated deciles are not the real deciles,
    so the check scored drift against its own baseline — NYC taxi `DOLocationID`
    reproduced at PSI 0.363, a `warn` on unchanged data.

    Two sentinel bins, ``[-inf, min)`` and ``[p99, inf)``, catch values outside the
    baseline's observed range. The lower one carries **zero** baseline mass: nothing in
    the baseline sits below the baseline's own minimum. Handing it 0.1 (the old
    behaviour) put ~0.69 PSI into every constant / zero-inflated / left-bounded column
    on data that never changed (#270) — a constant column scored 0.7006.

    Ties still matter and are still pooled forward: when several quantiles land on the
    same value (a 70%-zero column collapses p1..p50 onto 0) their masses accumulate in
    the single bin that starts at that value, which is exactly ``P(v <= x < next)`` for
    half-open bins. Without pooling a merged bin would be scored at one segment's mass
    while really carrying several.
    """
    pts: list[tuple[float, float]] = []
    for k, v in (bcol.get("quantiles") or {}).items():
        prob, val = _finite_float(k), _finite_float(v)
        if prob is not None and val is not None and 0.0 < prob < 1.0:
            pts.append((prob, val))
    pts.sort()
    if len(pts) < 2:
        return None
    probs = np.array([p for p, _ in pts])
    # A quantile function is non-decreasing; enforce it so a rounded/garbled profile
    # can't produce non-monotonic edges (np.histogram raises on those).
    vals = np.maximum.accumulate(np.array([v for _, v in pts]))

    lo = _finite_float(bcol.get("min"))
    lo = min(lo, float(vals[0])) if lo is not None else -np.inf
    # Baseline segments: [min, p1), [p1, p5), ... [p99, inf) with the exact mass of each.
    raw = np.concatenate(([lo], vals, [np.inf]))
    masses = np.concatenate(([probs[0]], np.diff(probs), [1.0 - probs[-1]]))

    support = np.unique(raw[:-1])
    edges = (
        np.concatenate((support, [np.inf]))
        if support[0] == -np.inf  # baseline min unknown: keep [-inf, p1) as a real bin
        else np.concatenate(([-np.inf], support, [np.inf]))
    )
    nbins = edges.size - 1
    if nbins < 2:
        return None
    expected = np.zeros(nbins)
    for k in range(raw.size - 1):
        a, b = raw[k], raw[k + 1]
        if b == np.inf:
            idx = nbins - 1
        elif a == -np.inf:
            idx = 0
        elif a == b:  # tied quantiles = a point mass -> the bin that starts at it
            idx = int(np.searchsorted(edges, a, side="right")) - 1
        else:
            idx = int(np.searchsorted(edges, a + (b - a) / 2.0, side="right")) - 1
        expected[min(max(idx, 0), nbins - 1)] += masses[k]
    total = float(expected.sum())
    if total <= 0:
        return None
    return edges, expected / total


def _drift_pass_detail(
    detail: str,
    method: str,
    threshold: float,
    baseline_profile_id: int | None = None,
    kind: str | None = None,
    note: str | None = None,
) -> CheckResult:
    metrics: dict[str, Any] = {
        "baseline_profile_id": baseline_profile_id,
        "method": method,
        "threshold": threshold,
        "score": None,
        "bins": [],
    }
    if kind is not None:
        metrics["kind"] = kind
    if note is not None:
        metrics["note"] = note
    return CheckResult(violation_count=0, rows_evaluated=None, metrics=metrics, detail=detail)


def _numeric_drift(values: pd.Series, bcol: dict[str, Any], threshold: float) -> CheckResult | None:
    """PSI of current numeric values vs the baseline profile's quantile distribution."""
    built = _quantile_bins(bcol)
    if built is None:
        return None
    edges, expected = built
    nbins = edges.size - 1
    nums = pd.to_numeric(values, errors="coerce").dropna().to_numpy()
    if nums.size == 0:
        return CheckResult(
            violation_count=0,
            rows_evaluated=0,
            metrics={
                "method": "psi",
                "kind": "numeric",
                "binning": "quantile",
                "score": None,
                "threshold": threshold,
                "bins": [],
                "note": "no current numeric values",
            },
            detail="no current numeric values to compare against baseline",
        )
    counts, _ = np.histogram(nums, bins=edges)
    actual = counts / counts.sum()
    score = _psi(expected, actual)
    bins = [
        {
            "range": f"[{_edge(edges[i])}, {_edge(edges[i + 1])})",
            "expected_pct": round(float(expected[i]), 4),
            "actual_pct": round(float(actual[i]), 4),
        }
        for i in range(nbins)
    ]
    drifted = score >= threshold
    return CheckResult(
        violation_count=1 if drifted else 0,
        rows_evaluated=int(nums.size),
        metrics={"method": "psi", "kind": "numeric", "binning": "quantile",
                 "score": round(score, 4), "threshold": threshold, "bins": bins},
        detail=f"PSI {score:.3f} (threshold {threshold})",
    )


def _edge(x: float) -> Any:
    if np.isinf(x):
        return "-inf" if x < 0 else "inf"
    return round(float(x), 4)


def _mix_key(value: Any, numeric: bool) -> Any | None:
    """Comparison key for one distinct value. Numeric columns compare as floats:
    the baseline stores JSON numbers (``5``) while pandas may hand back ``5.0`` for
    the same column, and ``str()`` on those two does not match."""
    if numeric:
        return _finite_float(value)
    return None if value is None else str(value)


def _prefers_value_mix(bcol: dict[str, Any], nonnull_total: int) -> bool:
    """Should a numeric column be compared value-by-value instead of by quantile bins?

    Yes when the profile's 10 stored top values cover all but a sliver of the column and
    the column has few enough distinct values that the rest is a tail rather than
    structure. Anything they do not cover is invisible on this path (see
    ``_VALUE_MIX_MIN_COVERAGE``), so "most of it" is not good enough.
    Quantile bins are blind to point masses — a 0/1 flag or a 4-value payment code has
    no meaningful decile, and comparing it by bins scored PSI > 1 against its own
    baseline (#265). ``top_values`` are exact counts, so the value mix reproduces the
    baseline exactly on unchanged data at any cardinality; coverage is what decides
    whether it still *describes* the column.
    """
    tops = bcol.get("top_values") or []
    distinct = _finite_float(bcol.get("distinct_count"))
    # Unknown cardinality (an older profile) stays on the quantile path: that is the
    # path with no cap on how much of the column it can describe.
    if not tops or distinct is None or not 0 < distinct <= _VALUE_MIX_MAX_DISTINCT:
        return False
    covered = float(sum(_finite_float(t.get("count")) or 0.0 for t in tops))
    total = max(float(nonnull_total), covered)
    return total > 0 and covered / total >= _VALUE_MIX_MIN_COVERAGE


def _observed_value_counts(
    ctx: CheckContext,
    *,
    numeric: bool,
    baseline_distinct: Any = None,
    max_distinct: int = _EXACT_COUNT_MAX_DISTINCT,
) -> tuple[dict[Any, int], int] | None:
    """Exact per-value counts for ``ctx.column`` over the WHOLE table, in one GROUP BY.

    The baseline ``top_values`` are full-table exact counts while the check's own read
    is capped at ``max_rows``, so on a table bigger than that cap the two sides describe
    different populations and PSI is non-zero on unchanged data — NYC taxi `payment_type`
    scored 0.31 comparing full-table shares against the first 50k rows. Re-counting in
    SQL puts both sides on the same population.

    Returns None — caller falls back to the sampled frame — when the column has more
    distinct values than we are willing to materialise, or the source can't answer.
    """
    if not ctx.column:
        return None
    if isinstance(baseline_distinct, int) and baseline_distinct > max_distinct:
        return None  # known-wide column: don't pay for a GROUP BY we'd throw away
    sql = (
        f"SELECT {ctx.col} AS v, COUNT(*) AS c FROM {ctx.ref} "
        f"WHERE {ctx.col} IS NOT NULL GROUP BY {ctx.col}"
    )
    try:
        res = ctx.connector.run_select(sql, limit=max_distinct + 1)
    except Exception:  # noqa: BLE001 - any source-side failure just means "use the sample"
        return None
    if len(res.rows) > max_distinct:
        return None
    counts: dict[Any, int] = {}
    total = 0
    for row in res.rows:
        n = int(row[1] or 0)
        total += n  # denominator counts every non-null row, keyed or not
        key = _mix_key(row[0], numeric)
        if key is not None:
            counts[key] = counts.get(key, 0) + n
    return counts, total


def _categorical_drift(
    values: pd.Series,
    bcol: dict[str, Any],
    nonnull_total: int,
    threshold: float,
    *,
    numeric: bool = False,
    observed: tuple[dict[Any, int], int] | None = None,
) -> CheckResult:
    """PSI of the current value mix vs the baseline top_values (+ __other__).

    ``top_values`` are *full-table* exact counts (profiler.py), so the baseline
    denominator must be the full-table non-null count — NOT the pandas sample size.
    Using the sample size made ``sum(top_counts) > total`` for columns with >10
    distinct values, collapsing the ``__other__`` expected mass to ~0 and inflating
    PSI on unchanged data (the 11–20-distinct band the generator targets).

    ``observed`` supplies exact current counts (see ``_observed_value_counts``); without
    it the current side is counted from the sampled frame, which is exact only while the
    read wasn't truncated."""
    tops = bcol.get("top_values") or []
    labels: list[str] = []
    keys: list[Any] = []
    counts: list[float] = []
    for t in tops:
        key = _mix_key(t.get("value"), numeric)
        if key is None:
            continue
        labels.append(str(t.get("value")))
        keys.append(key)
        counts.append(float(t.get("count") or 0))
    base_counts = np.array(counts, dtype=float)
    base_total = max(float(nonnull_total), float(base_counts.sum()), 1.0)
    expected = np.append(base_counts / base_total, max(0.0, 1.0 - base_counts.sum() / base_total))

    if observed is not None:
        obs_counts, cur_total = observed
    else:
        cur = values.dropna()
        cur = pd.to_numeric(cur, errors="coerce").dropna() if numeric else cur.astype(str)
        obs_counts = {
            (float(k) if numeric else str(k)): int(v) for k, v in cur.value_counts().items()
        }
        cur_total = int(len(cur))
    denom = max(cur_total, 1)
    actual_named = np.array([float(obs_counts.get(k, 0)) for k in keys]) / denom
    actual_other = max(0.0, 1.0 - actual_named.sum())
    actual = np.append(actual_named, actual_other)

    score = _psi(expected, actual)
    bin_labels = [*labels, "__other__"]
    bins = [
        {"category": bin_labels[i], "expected_pct": round(float(expected[i]), 4),
         "actual_pct": round(float(actual[i]), 4)}
        for i in range(len(bin_labels))
    ]
    drifted = score >= threshold
    return CheckResult(
        violation_count=1 if drifted else 0,
        rows_evaluated=cur_total,
        metrics={"method": "psi", "kind": "numeric" if numeric else "categorical",
                 "binning": "value", "score": round(score, 4),
                 "threshold": threshold, "bins": bins},
        detail=f"PSI {score:.3f} (threshold {threshold})",
    )


def _reservoir_sample(values: np.ndarray, k: int = _DRIFT_SAMPLE_CAP) -> list[float]:
    if values.size <= k:
        return [float(v) for v in values]
    rng = np.random.default_rng(0)  # deterministic sub-sample
    idx = rng.choice(values.size, size=k, replace=False)
    return [float(v) for v in values[idx]]


def _ks_drift(values: pd.Series, ctx: CheckContext, threshold: float) -> CheckResult:
    """KS two-sample test of current numeric values vs the PREVIOUS run's stored
    reservoir sample. Baseline raw values aren't persisted in the profile, so KS
    drift is measured run-over-run: the first run captures a sample and passes."""
    from scipy.stats import ks_2samp  # transitively available via scikit-learn

    from app.models import CheckRun  # local import to avoid a cycle

    nums = pd.to_numeric(values, errors="coerce").dropna().to_numpy()
    current_sample = _reservoir_sample(nums)

    prior: list[float] = []
    if ctx.db is not None and ctx.check_id is not None:
        run = (
            ctx.db.query(CheckRun)
            .filter(CheckRun.check_id == ctx.check_id, CheckRun.status != "error")
            .order_by(CheckRun.started_at.desc())
            .first()
        )
        if run and run.metrics:
            prior = [float(x) for x in (run.metrics.get("drift_sample") or [])]

    base_metrics = {
        "method": "ks",
        "kind": "numeric",
        "baseline_profile_id": None,
        "score": None,
        "threshold": threshold,
        "drift_sample": current_sample,  # persisted by the runner into run.metrics
        "current_sample_n": len(current_sample),
    }
    if len(prior) < 2 or len(current_sample) < 2:
        return CheckResult(
            violation_count=0,
            rows_evaluated=int(nums.size),
            metrics={**base_metrics, "prior_n": len(prior), "note": "baseline captured"},
            detail="KS baseline captured (first run) — drift measured from next run",
        )
    res = ks_2samp(current_sample, prior)
    pvalue = float(res.pvalue)
    statistic = float(res.statistic)
    drifted = pvalue <= threshold
    return CheckResult(
        violation_count=1 if drifted else 0,
        rows_evaluated=int(nums.size),
        metrics={**base_metrics, "score": round(pvalue, 6), "statistic": round(statistic, 4),
                 "prior_n": len(prior)},
        detail=f"KS p-value {pvalue:.4g} vs previous run (D={statistic:.3f}, fails when p<={threshold})",
    )


def _run_distribution_drift(ctx: CheckContext) -> CheckResult:
    settings = get_settings()
    method = str(ctx.params.get("method") or "psi").lower()
    threshold = float(ctx.params.get("threshold", 0.2))
    raw_max = ctx.params.get("max_rows")
    max_rows = int(raw_max) if raw_max not in (None, "") else int(settings.ml_max_rows)

    # THE COHERENCE RULE (#269). The PSI baseline is the profiler's stored quantiles,
    # which are computed on the profiler's SAMPLE. If the two sides are drawn
    # differently, PSI measures the difference between the two *reads* rather than any
    # change in the data. That is why both used to be a bare LIMIT: agreeing on a biased
    # slice is what took the taxi false fires to 0. Now that the profiler samples
    # properly, this read must sample properly too — with the same helper and the same
    # seed — or the bias comes straight back as drift on unchanged tables.
    #
    # `reproducible_only` for KS: KS compares this run's sample against the PREVIOUS
    # RUN's stored sample, i.e. two reads of the same table at two times. An un-seeded
    # random draw on each run would differ by sampling noise alone and reject at the
    # configured p-threshold on data nobody touched, so KS keeps a draw that repeats.
    row_count = ctx.connector.row_count(ctx.table, ctx.schema)
    sample = ctx.connector.sample_df(
        f"{ctx.col} AS v", ctx.ref, max_rows,
        row_count=row_count, reproducible_only=(method == "ks"),
    )
    values = sample.df["v"]

    if method == "ks":
        return _ks_drift(values, ctx, threshold)

    profile = _latest_baseline_profile(ctx)
    if profile is None:
        return _drift_pass_detail(
            "no baseline profile - profile the dataset first",
            method,
            threshold,
            note="missing baseline profile",
        )
    bcol = _baseline_column(profile, ctx.column or "")
    if bcol is None:
        return _drift_pass_detail(
            f"column {ctx.column!r} not found in baseline profile #{profile.id}",
            method,
            threshold,
            baseline_profile_id=profile.id,
            note="column missing from baseline profile",
        )
    has_quantiles = bool(bcol.get("quantiles"))
    has_top_values = bool(bcol.get("top_values"))
    # Baseline denominator = full-table non-null count (top_values are full-table
    # exact counts), not the pandas sample size.
    nonnull_total = int(profile.row_count or 0) - int(bcol.get("null_count") or 0)
    if has_top_values and (not has_quantiles or _prefers_value_mix(bcol, nonnull_total)):
        # Only re-count in SQL when the read really is a sample; when it isn't, the
        # frame already IS the whole column and the extra scan would buy nothing.
        # `sample.truncated` compares the row count to the cap, so it stays right for a
        # percentage-based sampler that lands a few rows short of `max_rows`.
        observed = (
            _observed_value_counts(
                ctx, numeric=has_quantiles, baseline_distinct=bcol.get("distinct_count")
            )
            if sample.truncated
            else None
        )
        result = _categorical_drift(
            values, bcol, nonnull_total, threshold, numeric=has_quantiles, observed=observed
        )
    elif has_quantiles:
        result = _numeric_drift(values, bcol, threshold)
        if result is None:
            return _drift_pass_detail(
                f"baseline profile #{profile.id} has no usable numeric distribution",
                method,
                threshold,
                baseline_profile_id=profile.id,
                kind="numeric",
                note="unusable numeric baseline",
            )
    else:
        return _drift_pass_detail(
            f"baseline profile #{profile.id} has neither quantiles nor categories for {ctx.column!r}",
            method,
            threshold,
            baseline_profile_id=profile.id,
            note="unusable baseline profile column",
        )

    result.metrics["baseline_profile_id"] = profile.id
    result.metrics["current_sampling"] = sample.as_facts(max_rows)
    score = result.metrics.get("score")
    result.detail = (
        f"PSI {score} vs baseline profile #{profile.id} (threshold {threshold})"
        if score is not None
        else result.detail
    )
    return result


# ---------------------------------------------------------------- schema_change
def _run_schema_change(ctx: CheckContext) -> CheckResult:
    """Detect column schema drift vs a baseline (issue #101).

    Baseline modes: ``previous`` (the schema this check saw on its last run,
    stored in CheckRun.metrics) or ``pinned`` (a SchemaSnapshot marked is_baseline).
    Each delta becomes a violating "row" so it flows through the normal triage
    workflow. rows_evaluated is None so a change alert is NOT auto-resolved on the
    next (matching) run — it stays for an analyst to acknowledge.
    """
    from app.core import schema_monitor as sm
    from app.models import Check, CheckRun

    p = ctx.params
    ignore = {str(x) for x in (p.get("ignore_columns") or [])}
    flags = {
        "added": bool(p.get("on_added", False)),
        "removed": bool(p.get("on_removed", True)),
        "type_changed": bool(p.get("on_type_change", True)),
        "nullability_changed": bool(p.get("on_nullability_change", True)),
        "reordered": bool(p.get("on_reorder", False)),
    }
    mode = str(p.get("baseline") or "previous").lower()

    full = sm.introspect_columns(ctx.connector, ctx.table, ctx.schema)
    current = [c for c in full if c["name"] not in ignore]
    cur_fp = sm.schema_fingerprint(current)

    dataset_id: int | None = None
    if ctx.db is not None and ctx.check_id is not None:
        chk = ctx.db.get(Check, ctx.check_id)
        dataset_id = chk.dataset_id if chk else None

    baseline: list[dict[str, Any]] | None = None
    if mode == "pinned":
        if ctx.db is not None and dataset_id is not None:
            pin = sm.latest_pinned_baseline(ctx.db, dataset_id)
            if pin is not None:
                baseline = [c for c in pin.columns if c["name"] not in ignore]
    elif ctx.db is not None and ctx.check_id is not None:  # previous run
        prev = (
            ctx.db.query(CheckRun)
            .filter(CheckRun.check_id == ctx.check_id, CheckRun.status != "error")
            .order_by(CheckRun.started_at.desc())
            .first()
        )
        if prev is not None and isinstance(prev.metrics, dict) and prev.metrics.get("schema"):
            baseline = [c for c in prev.metrics["schema"] if c["name"] not in ignore]

    # Record the current schema for the history timeline (deduped, full schema).
    # On the first pinned run with no baseline yet, establish one to enforce against
    # (pin first so the dedupe collapses the two into a single row).
    if ctx.db is not None and dataset_id is not None:
        if mode == "pinned" and baseline is None:
            sm.pin_baseline(ctx.db, dataset_id, full)
        sm.capture_schema_snapshot(ctx.db, dataset_id, full, source="check")

    base_metrics = {"baseline": mode, "schema": current, "schema_fingerprint": cur_fp, "column_count": len(current)}

    if baseline is None:
        return CheckResult(
            violation_count=0,
            rows_evaluated=None,
            metrics={**base_metrics, "note": "baseline captured"},
            detail="Schema baseline captured — changes flagged from the next run",
        )

    delta = sm.diff_schemas(baseline, current)
    sample_rows: list[dict[str, Any]] = []
    reasons: list[str] = []

    def _add(kind: str, column: str | None, frm: Any, to: Any, msg: str) -> None:
        sample_rows.append({"change_type": kind, "column": column, "from": frm, "to": to})
        reasons.append(msg)

    if flags["removed"]:
        for col in delta["removed"]:
            _add("removed", col["name"], col.get("dtype"), None, f"column '{col['name']}' was removed")
    if flags["type_changed"]:
        for ch in delta["type_changed"]:
            _add("type_changed", ch["column"], ch["from"], ch["to"],
                 f"column '{ch['column']}' type changed {ch['from']} → {ch['to']}")
    if flags["nullability_changed"]:
        for ch in delta["nullability_changed"]:
            frm, to = ("NULL" if ch["from"] else "NOT NULL"), ("NULL" if ch["to"] else "NOT NULL")
            _add("nullability_changed", ch["column"], ch["from"], ch["to"],
                 f"column '{ch['column']}' nullability changed {frm} → {to}")
    if flags["added"]:
        for col in delta["added"]:
            _add("added", col["name"], None, col.get("dtype"), f"new column '{col['name']}' ({col.get('dtype')})")
    if flags["reordered"] and delta["reordered"]:
        _add("reordered", None, None, None, "column order changed")

    metrics = {
        **base_metrics,
        "added": [c["name"] for c in delta["added"]],
        "removed": [c["name"] for c in delta["removed"]],
        "type_changed": delta["type_changed"],
        "nullability_changed": delta["nullability_changed"],
        "reordered": delta["reordered"],
    }
    any_delta = any(delta[k] for k in ("added", "removed", "type_changed", "nullability_changed")) or delta["reordered"]
    if sample_rows:
        detail = f"{len(sample_rows)} schema change(s) vs {mode} baseline: " + "; ".join(reasons[:4])
    else:
        detail = "Schema matches baseline" if not any_delta else "Only non-alerting schema changes"
    return CheckResult(
        violation_count=len(sample_rows),
        rows_evaluated=None,
        sample_rows=sample_rows,
        reasons=reasons,
        metrics=metrics,
        detail=detail,
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
            "schema_contract", "Schema contract",
            "Current table columns must match an expected contract", False,
            [_p("expected_columns", "list", True, [], "Expected columns from profile/metadata"),
             _p("allow_additive", "boolean", False, True, "Allow extra current columns"),
             _p("case_sensitive", "boolean", False, False, "Compare column names case-sensitively")],
            _run_schema_contract,
        ),
        CheckType(
            "freshness", "Freshness", "Newest timestamp must be recent", True,
            [_p("max_age_hours", "number", False, 24, "Maximum age of newest row"),
             _p("strategy", "string", False, "static", "static | adaptive"),
             _p("default_max_age_hours", "number", False, 24, "Fallback age for adaptive mode"),
             _p("min_history", "number", False, 3, "Prior runs required for adaptive threshold"),
             _p("lookback_runs", "number", False, 14, "Prior runs to inspect"),
             _p("multiplier", "number", False, 2.0, "Cadence multiplier for adaptive threshold"),
             _p("grace_hours", "number", False, 0, "Extra hours added to adaptive threshold")],
            _run_freshness,
        ),
        CheckType(
            "row_count_min", "Minimum row count", "Table must have at least N rows", False,
            [_p("min_rows", "number", True, 1)],
            _run_row_count_min,
        ),
        CheckType(
            "row_count_anomaly", "Row count anomaly", "Row count must track its recent history", False,
            [_p("strategy", "string", False, "sigma", "sigma | adaptive"),
             _p("sigma", "number", False, 3.0), _p("lookback_runs", "number", False, 14),
             _p("min_history", "number", False, 5),
             _p("multiplier", "number", False, 3.5, "MAD multiplier for adaptive bounds")],
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
        CheckType(
            "distribution_drift", "Distribution drift",
            "Alert when the column's distribution shifts vs the profiling baseline (PSI or KS)", True,
            [_p("method", "string", False, "psi", "psi | ks"),
             _p("threshold", "number", False, 0.2,
                "PSI >= threshold (or KS p-value <= threshold when method=ks) fails"),
             _p("max_rows", "number", False, None, "Sample cap for current data (default settings.ml_max_rows)")],
            _run_distribution_drift,
        ),
        CheckType(
            "schema_change", "Schema change",
            "Alert when the column schema drifts (added/removed/retyped/nullability/reorder)", False,
            [_p("baseline", "string", False, "previous", "previous | pinned"),
             _p("on_removed", "boolean", False, True, "Flag removed columns"),
             _p("on_type_change", "boolean", False, True, "Flag column type changes"),
             _p("on_nullability_change", "boolean", False, True, "Flag NULL/NOT NULL changes"),
             _p("on_added", "boolean", False, False, "Flag new columns"),
             _p("on_reorder", "boolean", False, False, "Flag column-order changes"),
             _p("ignore_columns", "list", False, None, "Column names to ignore")],
            _run_schema_change,
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
    if check_type == "accepted_values":
        values = normalized.get("values")
        if not isinstance(values, list):
            raise ValueError("accepted_values 'values' must be a list")
    if check_type == "custom_sql":
        guard_sql(normalized["sql"])
    if check_type == "regex_match":
        try:
            re.compile(normalized["pattern"])
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern: {exc}") from exc
    if check_type == "schema_contract":
        expected = _contract_columns(normalized.get("expected_columns") or [])
        if not expected:
            raise ValueError("schema_contract requires non-empty 'expected_columns'")
        case_sensitive = _bool_param(normalized.get("case_sensitive"), False)
        keys = [_contract_key(c["name"], case_sensitive) for c in expected]
        if len(keys) != len(set(keys)):
            raise ValueError("schema_contract expected_columns contains duplicate column names")
        normalized["expected_columns"] = expected
        normalized["allow_additive"] = _bool_param(normalized.get("allow_additive"), True)
        normalized["case_sensitive"] = case_sensitive
    if check_type == "freshness":
        strategy = str(normalized.get("strategy") or "static").lower()
        if strategy not in ("static", "adaptive"):
            raise ValueError("freshness 'strategy' must be 'static' or 'adaptive'")
        if "strategy" in normalized or strategy == "adaptive":
            normalized["strategy"] = strategy
        if strategy == "adaptive" and "default_max_age_hours" not in normalized:
            normalized["default_max_age_hours"] = normalized.get("max_age_hours", 24)
    if check_type == "row_count_anomaly":
        strategy = str(normalized.get("strategy") or "sigma").lower()
        if strategy == "robust":
            strategy = "adaptive"
        if strategy not in ("sigma", "adaptive"):
            raise ValueError("row_count_anomaly 'strategy' must be 'sigma' or 'adaptive'")
        if "strategy" in normalized or strategy == "adaptive":
            normalized["strategy"] = strategy
    if check_type == "distribution_drift":
        method = str(normalized.get("method") or "psi").lower()
        if method not in ("psi", "ks"):
            raise ValueError("distribution_drift 'method' must be 'psi' or 'ks'")
        normalized["method"] = method
    if check_type == "schema_change":
        mode = str(normalized.get("baseline") or "previous").lower()
        if mode not in ("previous", "pinned"):
            raise ValueError("schema_change 'baseline' must be 'previous' or 'pinned'")
        normalized["baseline"] = mode
    return normalized


def run_check_type(ctx: CheckContext, check_type: str) -> CheckResult:
    return CHECK_TYPES[check_type].run(ctx)
