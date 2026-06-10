"""Heuristic investigation-query suggestions.

Given a dataset profile and optional failure context (check / run / exception),
produce runnable, guarded SELECTs an analyst would write next. Also serves as
the no-LLM fallback for /query/suggest.
"""

from typing import Any

from app.connectors.safety import guard_sql


def day_expr(kind: str, col: str) -> str:
    if kind == "sqlite":
        return f"DATE({col})"
    return f"CAST({col} AS DATE)"


def _cols(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    return (profile or {}).get("columns", [])


def _best_segment(profile: dict[str, Any] | None, exclude: str | None = None) -> str | None:
    """A low-cardinality string column to break counts down by."""
    candidates = [
        c
        for c in _cols(profile)
        if c["kind"] == "string" and 2 <= c["distinct_count"] <= 20 and c["name"] != exclude
    ]
    candidates.sort(key=lambda c: c["distinct_count"])
    return candidates[0]["name"] if candidates else None


def _temporal(profile: dict[str, Any] | None) -> str | None:
    facts = (profile or {}).get("table_facts", {})
    temporal = facts.get("temporal_columns") or []
    return temporal[0]["name"] if temporal else None


def _q(connector, name: str) -> str:
    return connector.quote(name)


def suggest_for_dataset(connector, ref: str, profile: dict[str, Any] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    tcol = _temporal(profile)
    seg = _best_segment(profile)

    if tcol:
        tq = _q(connector, tcol)
        out.append(
            {
                "title": "Most recent rows",
                "sql": f"SELECT * FROM {ref}\nORDER BY {tq} DESC\nLIMIT 50",
                "rationale": "Eyeball the newest data — most issues arrive with the latest load.",
            }
        )
        out.append(
            {
                "title": "Daily row volume (last 30 loaded days)",
                "sql": (
                    f"SELECT {day_expr(connector.kind, tq)} AS day, COUNT(*) AS n\n"
                    f"FROM {ref}\nGROUP BY 1\nORDER BY 1 DESC\nLIMIT 30"
                ),
                "rationale": "Volume gaps and spikes are the most common upstream-pipeline symptom.",
            }
        )
    if seg:
        sq = _q(connector, seg)
        out.append(
            {
                "title": f"Row counts by {seg}",
                "sql": f"SELECT {sq}, COUNT(*) AS n\nFROM {ref}\nGROUP BY 1\nORDER BY n DESC\nLIMIT 25",
                "rationale": "Issues are usually concentrated in one segment, not uniform.",
            }
        )

    nully = [c for c in _cols(profile) if c["null_pct"] > 0]
    nully.sort(key=lambda c: c["null_pct"], reverse=True)
    if nully:
        col = nully[0]["name"]
        cq = _q(connector, col)
        seg2 = _best_segment(profile, exclude=col)
        if seg2:
            s2 = _q(connector, seg2)
            out.append(
                {
                    "title": f"Where are the NULL {col} rows concentrated?",
                    "sql": (
                        f"SELECT {s2}, COUNT(*) AS null_rows\nFROM {ref}\n"
                        f"WHERE {cq} IS NULL\nGROUP BY 1\nORDER BY null_rows DESC\nLIMIT 25"
                    ),
                    "rationale": f"{col} is {nully[0]['null_pct']:.1%} NULL — segmenting localizes the cause.",
                }
            )

    numerics = [c for c in _cols(profile) if c["kind"] == "numeric" and c.get("stddev")]
    if numerics:
        col = max(numerics, key=lambda c: abs(c.get("stddev") or 0))["name"]
        cq = _q(connector, col)
        out.append(
            {
                "title": f"Extreme values of {col}",
                "sql": f"SELECT * FROM {ref}\nWHERE {cq} IS NOT NULL\nORDER BY {cq} DESC\nLIMIT 25",
                "rationale": "Magnitude typos (100x) and unit mix-ups live at the extremes.",
            }
        )
    return out


def suggest_for_check(
    connector,
    ref: str,
    profile: dict[str, Any] | None,
    check_type: str,
    column: str | None,
    params: dict[str, Any],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    tcol = _temporal(profile)
    seg = _best_segment(profile, exclude=column)
    cq = _q(connector, column) if column else None

    def by_segment_and_time(where: str, label: str) -> None:
        if seg:
            sq = _q(connector, seg)
            out.append(
                {
                    "title": f"{label} by {seg}",
                    "sql": f"SELECT {sq}, COUNT(*) AS n\nFROM {ref}\nWHERE {where}\nGROUP BY 1\nORDER BY n DESC\nLIMIT 25",
                    "rationale": "Concentration in one segment points at a specific upstream source.",
                }
            )
        if tcol:
            tq = _q(connector, tcol)
            out.append(
                {
                    "title": f"{label} by day — when did it start?",
                    "sql": (
                        f"SELECT {day_expr(connector.kind, tq)} AS day, COUNT(*) AS n\n"
                        f"FROM {ref}\nWHERE {where}\nGROUP BY 1\nORDER BY 1 DESC\nLIMIT 30"
                    ),
                    "rationale": "A sharp start date usually maps to a deploy or source change.",
                }
            )

    if check_type == "not_null" and cq:
        out.append(
            {
                "title": f"Sample rows where {column} IS NULL",
                "sql": f"SELECT * FROM {ref}\nWHERE {cq} IS NULL\nLIMIT 50",
                "rationale": "Look for what the violating rows have in common.",
            }
        )
        by_segment_and_time(f"{cq} IS NULL", f"NULL {column}")
    elif check_type == "unique" and cq:
        out.append(
            {
                "title": f"Duplicated {column} values",
                "sql": (
                    f"SELECT {cq} AS value, COUNT(*) AS occurrences\nFROM {ref}\n"
                    f"WHERE {cq} IS NOT NULL\nGROUP BY 1\nHAVING COUNT(*) > 1\nORDER BY occurrences DESC\nLIMIT 50"
                ),
                "rationale": "Retries and re-loads produce exact duplicates; merges produce near-duplicates.",
            }
        )
    elif check_type == "range" and cq:
        lo, hi = params.get("min"), params.get("max")
        parts = []
        if lo is not None:
            parts.append(f"{cq} < {lo}")
        if hi is not None:
            parts.append(f"{cq} > {hi}")
        where = " OR ".join(parts) or f"{cq} IS NOT NULL"
        out.append(
            {
                "title": f"Out-of-range {column} rows",
                "sql": f"SELECT * FROM {ref}\nWHERE {where}\nLIMIT 50",
                "rationale": "Inspect offenders directly before deciding bad-data vs bad-threshold.",
            }
        )
        by_segment_and_time(where, f"Out-of-range {column}")
    elif check_type == "freshness" and cq:
        out.append(
            {
                "title": f"Latest {column} per day",
                "sql": (
                    f"SELECT {day_expr(connector.kind, cq)} AS day, COUNT(*) AS n, MAX({cq}) AS latest\n"
                    f"FROM {ref}\nGROUP BY 1\nORDER BY 1 DESC\nLIMIT 30"
                ),
                "rationale": "Shows whether the feed stopped entirely or slowed to a trickle.",
            }
        )
    elif check_type == "accepted_values" and cq:
        out.append(
            {
                "title": f"All distinct {column} values",
                "sql": f"SELECT {cq} AS value, COUNT(*) AS n\nFROM {ref}\nGROUP BY 1\nORDER BY n DESC\nLIMIT 50",
                "rationale": "New/unexpected values are often casing or whitespace variants of valid ones.",
            }
        )
    elif check_type == "ml_outlier":
        numerics = [c["name"] for c in _cols(profile) if c["kind"] == "numeric"][:3]
        for col in numerics:
            q = _q(connector, col)
            out.append(
                {
                    "title": f"Top {col} extremes",
                    "sql": f"SELECT * FROM {ref}\nWHERE {q} IS NOT NULL\nORDER BY {q} DESC\nLIMIT 20",
                    "rationale": "Outliers usually trace back to one numeric column's extremes.",
                }
            )
    elif check_type == "custom_sql" and params.get("sql"):
        out.append(
            {
                "title": "The check's own violation query",
                "sql": str(params["sql"]),
                "rationale": "Start from the exact rows the check flagged.",
            }
        )

    out.extend(suggest_for_dataset(connector, ref, profile))
    return out


def validated(suggestions: list[dict[str, str]], cap: int = 8) -> list[dict[str, str]]:
    """Keep only guard-passing, deduped suggestions."""
    seen: set[str] = set()
    out = []
    for s in suggestions:
        sql = (s.get("sql") or "").strip()
        title = (s.get("title") or "").strip()
        if not sql or not title or title in seen:
            continue
        try:
            guard_sql(sql)
        except Exception:  # noqa: BLE001 - drop anything the guard rejects
            continue
        seen.add(title)
        out.append({"title": title, "sql": sql, "rationale": s.get("rationale", "")})
        if len(out) >= cap:
            break
    return out
