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
    """Newest timestamp must be recent. Future-dated rows (themselves a DQ smell)
    are excluded from the staleness computation and reported as a metric, so a
    handful of bad future dates can't mask a stale table.
    """
    max_age = float(ctx.params.get("max_age_hours", 24))
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
    latest = pd.to_datetime(str(latest_raw), errors="coerce", utc=True)
    if latest is pd.NaT:
        return CheckResult(violation_count=1, detail=f"Cannot parse {latest_raw!r} as timestamp")
    age_h = (datetime.now(UTC) - latest.to_pydatetime()).total_seconds() / 3600
    stale = age_h > max_age
    return CheckResult(
        violation_count=1 if stale else 0,
        metrics={
            "latest": str(latest),
            "age_hours": round(age_h, 2),
            "max_age_hours": max_age,
            "future_rows": future_rows,
        },
        detail=(
            f"Newest {ctx.column} is {age_h:.1f}h old (SLA {max_age}h)"
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


# ---------------------------------------------------------------- distribution_drift
_PSI_EPS = 1e-4  # epsilon-clamp for empty bins so ln(a/e) stays finite
_DRIFT_SAMPLE_CAP = 2000  # reservoir sample size persisted for the KS path


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


def _decile_edges(quantiles: dict[str, Any]) -> np.ndarray | None:
    """Build 9 inner decile edges (p10..p90) from a profile's stored quantiles.

    The profiler stores p1/p5/p25/p50/p75/p95/p99 (see profiler.py), not deciles,
    so we interpolate the quantile function at the deciles. Edges are made strictly
    increasing; degenerate (single-value) columns return None.
    """
    pts = sorted(
        (float(k), float(v))
        for k, v in quantiles.items()
        if v is not None and not (isinstance(v, float) and (np.isnan(v) or np.isinf(v)))
    )
    if len(pts) < 2:
        return None
    probs = np.array([p for p, _ in pts])
    vals = np.array([v for _, v in pts])
    inner = np.interp(np.arange(1, 10) / 10.0, probs, vals)
    edges = np.concatenate(([-np.inf], inner, [np.inf]))
    # collapse ties so np.histogram gets monotonic edges; a near-constant column
    # (all edges equal) can't form a distribution → signal "no usable baseline".
    edges = np.unique(edges)
    if edges.size < 3:
        return None
    return edges


def _numeric_drift(values: pd.Series, bcol: dict[str, Any], threshold: float) -> CheckResult | None:
    """PSI of current numeric values vs the baseline profile's decile distribution."""
    edges = _decile_edges(bcol.get("quantiles") or {})
    if edges is None:
        return None
    nbins = edges.size - 1
    expected = np.full(nbins, 1.0 / nbins)  # deciles are equiprobable by construction
    nums = pd.to_numeric(values, errors="coerce").dropna().to_numpy()
    if nums.size == 0:
        return CheckResult(
            violation_count=0,
            rows_evaluated=0,
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
        metrics={"method": "psi", "kind": "numeric", "score": round(score, 4),
                 "threshold": threshold, "bins": bins},
        detail=f"PSI {score:.3f} (threshold {threshold})",
    )


def _edge(x: float) -> Any:
    if np.isinf(x):
        return "-inf" if x < 0 else "inf"
    return round(float(x), 4)


def _categorical_drift(
    values: pd.Series, bcol: dict[str, Any], sampled_rows: int, threshold: float
) -> CheckResult:
    """PSI of current category mix vs the baseline top_values (+ __other__)."""
    tops = bcol.get("top_values") or []
    cats = [str(t["value"]) for t in tops]
    base_counts = np.array([float(t["count"]) for t in tops])
    base_total = float(sampled_rows) if sampled_rows else float(base_counts.sum())
    base_total = max(base_total, base_counts.sum(), 1.0)
    expected = np.append(base_counts / base_total, max(0.0, 1.0 - base_counts.sum() / base_total))

    cur = values.dropna().astype(str)
    cur_total = max(len(cur), 1)
    vc = cur.value_counts()
    actual_named = np.array([float(vc.get(c, 0)) for c in cats]) / cur_total
    actual_other = max(0.0, 1.0 - actual_named.sum())
    actual = np.append(actual_named, actual_other)

    score = _psi(expected, actual)
    labels = [*cats, "__other__"]
    bins = [
        {"category": labels[i], "expected_pct": round(float(expected[i]), 4),
         "actual_pct": round(float(actual[i]), 4)}
        for i in range(len(labels))
    ]
    drifted = score >= threshold
    return CheckResult(
        violation_count=1 if drifted else 0,
        rows_evaluated=int(len(cur)),
        metrics={"method": "psi", "kind": "categorical", "score": round(score, 4),
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
        "threshold": threshold,
        "drift_sample": current_sample,  # persisted by the runner into run.metrics
    }
    if len(prior) < 2 or len(current_sample) < 2:
        return CheckResult(
            violation_count=0,
            rows_evaluated=int(nums.size),
            metrics={**base_metrics, "note": "baseline captured"},
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

    values = ctx.connector.fetch_df(f"SELECT {ctx.col} AS v FROM {ctx.ref}", limit=max_rows)["v"]

    if method == "ks":
        return _ks_drift(values, ctx, threshold)

    profile = _latest_baseline_profile(ctx)
    if profile is None:
        return CheckResult(violation_count=0, detail="no baseline profile — profile the dataset first")
    bcol = _baseline_column(profile, ctx.column or "")
    if bcol is None:
        return CheckResult(
            violation_count=0,
            detail=f"column {ctx.column!r} not found in baseline profile #{profile.id}",
        )

    if bcol.get("quantiles"):
        result = _numeric_drift(values, bcol, threshold)
        if result is None:  # degenerate baseline (constant column) — can't bin
            return CheckResult(
                violation_count=0,
                detail=f"baseline profile #{profile.id} has no usable numeric distribution",
            )
    elif bcol.get("top_values"):
        result = _categorical_drift(values, bcol, profile.sampled_rows, threshold)
    else:
        return CheckResult(
            violation_count=0,
            detail=f"baseline profile #{profile.id} has neither quantiles nor categories for {ctx.column!r}",
        )

    result.metrics["baseline_profile_id"] = profile.id
    score = result.metrics.get("score")
    result.detail = (
        f"PSI {score} vs baseline profile #{profile.id} (threshold {threshold})"
        if score is not None
        else result.detail
    )
    return result


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
        CheckType(
            "distribution_drift", "Distribution drift",
            "Alert when the column's distribution shifts vs the profiling baseline (PSI or KS)", True,
            [_p("method", "string", False, "psi", "psi | ks"),
             _p("threshold", "number", False, 0.2,
                "PSI >= threshold (or KS p-value <= threshold when method=ks) fails"),
             _p("max_rows", "number", False, None, "Sample cap for current data (default settings.ml_max_rows)")],
            _run_distribution_drift,
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
    if check_type == "distribution_drift":
        method = str(normalized.get("method") or "psi").lower()
        if method not in ("psi", "ks"):
            raise ValueError("distribution_drift 'method' must be 'psi' or 'ks'")
        normalized["method"] = method
    return normalized


def run_check_type(ctx: CheckContext, check_type: str) -> CheckResult:
    return CHECK_TYPES[check_type].run(ctx)
