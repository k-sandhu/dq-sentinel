"""Heuristic check generation from a profile — the deterministic baseline that
works without any LLM. Proposals are deduped against existing checks by the API.
"""

from typing import Any

ID_HINTS = ("id", "key", "code", "sku", "uuid")
MONEY_QTY_HINTS = ("price", "amount", "total", "qty", "quantity", "count", "cost", "fee")

PATTERN_REGEX = {
    "email": r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$",
    "uuid": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    "url": r"^https?://\S+$",
}


def _proposal(
    check_type: str,
    column: str | None,
    params: dict[str, Any],
    severity: str,
    rationale: str,
    schedule_minutes: int = 1440,
) -> dict[str, Any]:
    return {
        "check_type": check_type,
        "column_name": column,
        "params": params,
        "severity": severity,
        "rationale": rationale,
        "schedule_kind": "interval",
        "schedule_expr": str(schedule_minutes),
    }


def heuristic_proposals(
    profile: dict[str, Any], knowledge: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    knowledge = knowledge or {}
    rows = profile.get("row_count", 0)
    facts = profile.get("table_facts", {})
    out: list[dict[str, Any]] = []

    for col in profile.get("columns", []):
        name = col["name"]
        kind = col["kind"]
        lname = name.lower()
        nn, distinct = col["null_pct"], col["distinct_count"]

        # not_null where data is currently fully populated
        if rows >= 100 and col["null_count"] == 0:
            sev = "error" if name in facts.get("pk_candidates", []) else "warn"
            out.append(_proposal("not_null", name, {}, sev, f"0 NULLs across {rows} profiled rows"))
        elif rows >= 100 and 0 < nn <= 0.02 and any(h in lname for h in ("email", "name", "phone")):
            out.append(
                _proposal(
                    "not_null", name, {"tolerance": col["null_count"]}, "warn",
                    f"Mostly populated ({nn:.2%} NULL) — alert if missingness grows past today's level",
                )
            )

        # uniqueness for key-like fully-distinct columns
        if rows >= 100 and distinct == rows - col["null_count"] and col["null_count"] == 0:
            if any(h in lname for h in ID_HINTS) or name in facts.get("pk_candidates", []):
                out.append(_proposal("unique", name, {}, "error", f"All {rows} profiled values distinct"))

        if kind == "numeric":
            lo, hi = col.get("min"), col.get("max")
            q = col.get("quantiles") or {}
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi > lo:
                if lo >= 0 and any(h in lname for h in MONEY_QTY_HINTS):
                    out.append(
                        _proposal(
                            "range", name, {"min": 0}, "error",
                            f"Observed minimum {lo} ≥ 0 and name suggests a non-negative measure",
                        )
                    )
                else:
                    span = hi - lo
                    pad = span * 0.5 if span else max(abs(hi), 1)
                    out.append(
                        _proposal(
                            "range", name, {"min": lo - pad, "max": hi + pad}, "warn",
                            f"Observed range [{lo}, {hi}]; alert outside padded bounds",
                        )
                    )
            if q.get("0.99") is not None and isinstance(hi, (int, float)):
                p99 = float(q["0.99"])
                if p99 > 0 and hi > p99 * 20:
                    out.append(
                        _proposal(
                            "range", name, {"max": p99 * 20}, "warn",
                            f"Max {hi} is {hi / p99:.0f}× the p99 ({p99:.2f}) — possible magnitude typos",
                        )
                    )

        if kind == "string":
            for pat, ratio in (col.get("patterns") or {}).items():
                if pat in PATTERN_REGEX and ratio >= 0.95:
                    sev = "warn" if ratio < 1.0 else "error"
                    out.append(
                        _proposal(
                            "regex_match", name, {"pattern": PATTERN_REGEX[pat]}, sev,
                            f"{ratio:.1%} of sampled values match {pat} format",
                        )
                    )
            if 0 < distinct <= 10 and rows >= 500 and not any(h in lname for h in ID_HINTS):
                values = [t["value"] for t in col.get("top_values", [])][:distinct]
                if values and len(values) == distinct:
                    out.append(
                        _proposal(
                            "accepted_values", name, {"values": values}, "warn",
                            f"Only {distinct} distinct values observed — treat as a closed domain",
                        )
                    )

    # table-level checks
    sla = knowledge.get("freshness_sla_hours")
    for t in facts.get("temporal_columns", []):
        lname = t["name"].lower()
        if any(h in lname for h in ("created", "updated", "date", "time", "_at", "ts")):
            out.append(
                _proposal(
                    "freshness", t["name"], {"max_age_hours": sla or 48}, "error" if sla else "warn",
                    ("Freshness SLA from table knowledge" if sla else "Temporal column — default 48h SLA"),
                    schedule_minutes=360,
                )
            )
            break  # one freshness check on the best candidate

    if rows >= 100:
        out.append(
            _proposal(
                "row_count_min", None, {"min_rows": max(1, rows // 2)}, "error",
                f"Table had {rows} rows when profiled; alert if it halves",
            )
        )
        out.append(
            _proposal(
                "row_count_anomaly", None, {"sigma": 3.0}, "warn",
                "Detect unusual row-count jumps/drops against recent history",
            )
        )

    numeric_cols = [c["name"] for c in profile.get("columns", []) if c["kind"] == "numeric"]
    if rows >= 500 and len(numeric_cols) >= 2:
        out.append(
            _proposal(
                "ml_outlier", None, {"contamination": 0.005}, "info",
                f"IsolationForest across numeric columns ({', '.join(numeric_cols[:6])}…)"
                if len(numeric_cols) > 6
                else f"IsolationForest across numeric columns ({', '.join(numeric_cols)})",
            )
        )
    return out
