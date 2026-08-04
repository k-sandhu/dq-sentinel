"""Profiling engine.

Exact facts (row count, null counts, distinct counts, min/max, top values) come from
SQL aggregates pushed to the source. Rich stats (quantiles, lengths, pattern ratios)
come from a bounded sample pulled into pandas. Output is a JSON-able dict matching
schemas.ColumnProfileOut.

The sample is drawn by ``Connector.sample_df`` — a real random sample, not the first
n rows (#269) — and how it was drawn is reported back in
``table_facts["sampling"]`` alongside which stats came from it. Nothing here may
present a sample-derived number as a population statistic without that label.
"""

import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from app.connectors.sa import Connector

PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$"),
    "uuid": re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    "url": re.compile(r"^https?://\S+$"),
    "date_like": re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?"),
    "numeric_string": re.compile(r"^-?\d+(\.\d+)?$"),
}

NUMERIC_TOKENS = ("int", "float", "double", "real", "numeric", "decimal", "number")
TEMPORAL_TOKENS = ("date", "time", "timestamp")
BOOL_TOKENS = ("bool",)

# Which profile fields are exact over the whole table and which are estimated from the
# sample. Shipped inside table_facts["sampling"] so a consumer (analyst, LLM prompt,
# check generator) can tell the two apart instead of reading every number as a
# population statistic (#269).
POPULATION_STATS = (
    "row_count", "null_count", "null_pct", "distinct_count", "distinct_pct",
    "min", "max", "top_values",
)
SAMPLE_STATS = (
    "mean", "stddev", "quantiles", "min_len", "avg_len", "max_len", "patterns",
    "sample_values",
)


def jsonable(v: Any) -> Any:
    """Convert numpy/pandas/datetime values to JSON-safe Python."""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, Decimal):
        # Same finiteness guard as the float branch above. PostgreSQL `numeric`
        # (and 14+ `Infinity`) can hold non-finite values, and json.dumps emits
        # them as the bare tokens NaN/Infinity, which are invalid JSON — the
        # INSERT into a JSON column then fails at commit, OUTSIDE runner.py's
        # error handling, so the check 500s with no run row recorded at all.
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return v.isoformat()
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if pd.isna(v):  # catches pd.NaT / pd.NA after the scalar types above
        return None
    return v


def classify_dtype(dtype: str, series: pd.Series | None = None) -> str:
    d = dtype.lower()
    if any(t in d for t in BOOL_TOKENS):
        return "boolean"
    if any(t in d for t in NUMERIC_TOKENS):
        return "numeric"
    if any(t in d for t in TEMPORAL_TOKENS):
        return "temporal"
    if series is not None and series.dtype.kind in "iufc":
        return "numeric"
    if series is not None and series.dtype.kind == "M":
        return "temporal"
    if "char" in d or "text" in d or "string" in d or d in ("varchar", "clob"):
        return "string"
    if series is not None and series.dtype == object:
        sample = series.dropna().head(50)
        if len(sample) and all(isinstance(x, str) for x in sample):
            return "string"
    return "other"


def _exact_temporal_max(prof: dict[str, Any], sample_max: Any) -> Any:
    """Full-table MAX() for a temporal column, normalised to ISO-8601.

    The exact SQL MIN/MAX are already computed a few lines above, so reporting the
    *sample's* max here was strictly worse information than the profile already held:
    on the 2.96M-row taxi table it served 2024-01-01T17:01 for a column whose real max
    is 2024-02-01, and freshness monitors are generated off this value (#269). Falls
    back to the sample max only when the exact one is missing or unparseable.
    """
    exact = prof.get("max")
    if exact is None:
        return sample_max
    ts = pd.to_datetime(exact, errors="coerce")
    return sample_max if pd.isna(ts) else jsonable(ts)


def profile_dataset(
    connector: Connector,
    table: str,
    schema: str | None,
    sample_rows: int = 50_000,
    top_k: int = 10,
) -> dict[str, Any]:
    ref = connector.table_ref(table, schema)
    row_count = connector.row_count(table, schema)
    columns_meta = connector.get_columns(table, schema)

    # A representative sample, not the first `sample_rows` rows: the physical head of a
    # large table is one clustered slice (usually the oldest), and mean/stddev/
    # quantiles computed on it were being served next to the exact full-table row
    # count as if they described the population (#269).
    sample = connector.sample_df("*", ref, sample_rows, row_count=row_count)
    df = sample.df
    sampled = len(df)

    col_profiles: list[dict[str, Any]] = []
    pk_candidates: list[str] = []
    temporal_columns: list[dict[str, Any]] = []

    for col in columns_meta:
        name, dtype = col["name"], col["dtype"]
        q = connector.quote(name)
        series = df[name] if name in df.columns else pd.Series(dtype=object)
        kind = classify_dtype(dtype, series)

        non_null = int(connector.scalar(f"SELECT COUNT({q}) FROM {ref}") or 0)
        distinct = int(connector.scalar(f"SELECT COUNT(DISTINCT {q}) FROM {ref}") or 0)
        null_count = row_count - non_null

        prof: dict[str, Any] = {
            "name": name,
            "dtype": dtype,
            "kind": kind,
            "null_count": null_count,
            "null_pct": round(null_count / row_count, 6) if row_count else 0.0,
            "distinct_count": distinct,
            "distinct_pct": round(distinct / row_count, 6) if row_count else 0.0,
            "quantiles": {},
            "patterns": {},
            "top_values": [],
            "sample_values": [],
        }

        if non_null:
            try:
                prof["min"] = jsonable(connector.scalar(f"SELECT MIN({q}) FROM {ref}"))
                prof["max"] = jsonable(connector.scalar(f"SELECT MAX({q}) FROM {ref}"))
            except Exception:
                prof["min"] = prof["max"] = None

        s = series.dropna()
        if kind == "numeric" and len(s):
            s_num = pd.to_numeric(s, errors="coerce").dropna()
            if len(s_num):
                prof["mean"] = jsonable(s_num.mean())
                prof["stddev"] = jsonable(s_num.std()) if len(s_num) > 1 else 0.0
                prof["quantiles"] = {
                    p: jsonable(s_num.quantile(float(p)))
                    for p in ("0.01", "0.05", "0.25", "0.5", "0.75", "0.95", "0.99")
                }
        elif kind == "string" and len(s):
            s_str = s.astype(str)
            lengths = s_str.str.len()
            prof["min_len"] = int(lengths.min())
            prof["avg_len"] = round(float(lengths.mean()), 2)
            prof["max_len"] = int(lengths.max())
            scan = s_str.head(5000)
            for pname, pat in PATTERNS.items():
                hits = scan.str.match(pat).mean()
                if hits >= 0.5:  # only report meaningful pattern signals
                    prof["patterns"][pname] = round(float(hits), 4)
            # SQLite/DuckDB often store timestamps as TEXT — surface them as temporal
            if prof["patterns"].get("date_like", 0) >= 0.95:
                s_dt = pd.to_datetime(scan, errors="coerce", format="mixed")
                if len(s_dt) and s_dt.notna().mean() >= 0.95:
                    temporal_columns.append(
                        {"name": name, "max": _exact_temporal_max(prof, jsonable(s_dt.max()))}
                    )
        elif kind == "temporal" and len(s):
            s_dt = pd.to_datetime(s, errors="coerce").dropna()
            if len(s_dt):
                temporal_columns.append(
                    {"name": name, "max": _exact_temporal_max(prof, jsonable(s_dt.max()))}
                )

        # Exact top values via SQL (cheap and dialect-portable)
        if distinct and kind in ("string", "numeric", "boolean") and distinct <= max(row_count, 1):
            try:
                res = connector.run_select(
                    f"SELECT {q} AS v, COUNT(*) AS c FROM {ref} WHERE {q} IS NOT NULL "
                    f"GROUP BY {q} ORDER BY c DESC LIMIT {top_k}"
                )
                prof["top_values"] = [{"value": jsonable(r[0]), "count": int(r[1])} for r in res.rows]
            except Exception:
                pass

        prof["sample_values"] = [jsonable(v) for v in s.drop_duplicates().head(5).tolist()]

        if row_count >= 100 and null_count == 0 and distinct == row_count:
            pk_candidates.append(name)

        col_profiles.append(prof)

    return {
        "row_count": row_count,
        "sampled_rows": sampled,
        "columns": col_profiles,
        "table_facts": {
            "pk_candidates": pk_candidates,
            "temporal_columns": temporal_columns,
            "column_count": len(col_profiles),
            "sampling": {
                **sample.as_facts(sample_rows),
                "sample_stats": list(SAMPLE_STATS),
                "population_stats": list(POPULATION_STATS),
            },
        },
    }


# `full` needs no entry: it reports sampled=False and never reaches here.
_SAMPLING_BLURB = {
    "reservoir": "a {n}-row uniform random sample",
    "bernoulli": "a {n}-row uniform random sample",
    "random_sort": "a {n}-row uniform random sample",
    "head": "the FIRST {n} rows — NOT a random sample, so they describe one slice of "
            "the table rather than all of it",
}


def _sampling_note(profile: dict[str, Any]) -> str:
    """One clause telling the model how much to trust the sample-derived numbers."""
    sampling = (profile.get("table_facts") or {}).get("sampling")
    n = profile.get("sampled_rows", 0)
    if not sampling:  # profile stored before #269 — claim only what is actually known
        return f"stats from {n}-row sample"
    if not sampling.get("sampled"):
        return f"stats over all {n} rows"
    source = _SAMPLING_BLURB.get(str(sampling.get("method")), "a {n}-row sample").format(n=n)
    return f"mean/stddev/quantiles from {source}; counts and min/max are exact"


def summarize_profile_for_llm(profile: dict[str, Any], pii_columns: list[str] | None = None) -> str:
    """Compact, PII-redacted textual profile for prompts."""
    pii = {c.lower() for c in (pii_columns or [])}
    lines = [f"rows={profile['row_count']} ({_sampling_note(profile)})"]
    for c in profile["columns"]:
        redact = c["name"].lower() in pii
        bits = [
            f"- {c['name']} ({c['dtype']}, {c['kind']})",
            f"null%={round(c['null_pct'] * 100, 2)}",
            f"distinct={c['distinct_count']}",
        ]
        if not redact:
            if c.get("min") is not None:
                bits.append(f"min={c['min']} max={c['max']}")
            if c.get("mean") is not None:
                bits.append(f"mean={c['mean']} std={c.get('stddev')}")
            if c.get("patterns"):
                bits.append("patterns=" + ",".join(f"{k}:{v}" for k, v in c["patterns"].items()))
            if c.get("top_values"):
                tops = ", ".join(f"{t['value']!r}x{t['count']}" for t in c["top_values"][:5])
                bits.append(f"top=[{tops}]")
        else:
            bits.append("[values redacted: PII]")
        lines.append(" ".join(bits))
    facts = profile.get("table_facts", {})
    if facts.get("pk_candidates"):
        lines.append(f"pk_candidates={facts['pk_candidates']}")
    if facts.get("temporal_columns"):
        lines.append(f"temporal_columns={[t['name'] for t in facts['temporal_columns']]}")
    return "\n".join(lines)
