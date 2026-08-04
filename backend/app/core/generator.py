"""Heuristic check generation from a profile — the deterministic baseline that
works without any LLM. Proposals are deduped against existing checks by the API.

Two rules govern what comes out of here, both learned the hard way:

* **A bound must never be derived from the anomaly it should catch (#256).** The
  profile is of *unvalidated* data: if it contains a -899 fare or a 312,722-mile
  taxi trip, an ``[observed min, observed max]`` bound writes that defect into the
  definition of "acceptable" and the check can never fire on it. Bounds therefore
  come from robust statistics (quantiles + a generous tail headroom), and the
  rationale states which basis was used — an analyst reviewing a proposal must not
  be told "observed range" when the number came from p01/p99.
* **One proposal per (check_type, column) leaves this module (#264).** The API's
  dedup keeps whichever proposal it sees first, so emission order used to decide
  silently which of two same-target proposals the analyst ever saw. Collisions are
  resolved here instead — merged where the proposals are complementary, otherwise
  by an explicit usefulness rule.
"""

import math
import re
from typing import Any

from app.core import ml

ID_HINTS = ("id", "key", "code", "sku", "uuid")

# Name tokens that make a numeric column a *non-negative measure*: a value below 0
# is a defect rather than an observation. Matched against name *tokens* (not raw
# substrings) so `taxi_zone_id` is not read as a tax and `account_balance` is not
# read as a count.
NON_NEGATIVE_HINTS = frozenset(
    {
        "price", "amount", "amt", "total", "subtotal", "cost", "spend", "revenue", "fee",
        "fare", "tip", "tax", "toll", "surcharge", "charge", "discount", "qty", "quantity",
        "count", "unit", "distance", "mile", "km", "duration", "weight", "volume", "size",
    }
)
# ...and the tokens that override the prior above: measures that are signed by
# nature, however money-like the rest of the name looks (`net_amount`, `fee_delta`).
SIGNED_HINTS = frozenset(
    {
        "delta", "change", "diff", "difference", "adjustment", "net", "balance", "profit",
        "loss", "margin", "variance", "pnl", "offset", "correction", "gain", "deviation",
    }
)

QUANTILE_KEYS = ("0.01", "0.05", "0.25", "0.5", "0.75", "0.95", "0.99")

# Headroom above p99 (below p01) for the robust fence, as a multiple of the extreme
# tail width (p99-p95) or of the bulk spread (IQR), whichever is larger. Deliberately
# generous: a noisy proposal costs analyst trust, and these run on unreviewed data.
# On a normal distribution the fence lands beyond 6σ (~2 rows per 10^9); on an
# exponential one beyond 12.6/λ (~3 rows per 10^6) — while still excluding the
# order-of-magnitude outliers this exists to catch.
TAIL_HEADROOM = 5.0
IQR_HEADROOM = 3.0

# Mirrors check_types._ML_ID_DISTINCT_PCT: distinct share above which an integer
# column is a surrogate key rather than a measurement, and so not an ML feature.
ML_ID_DISTINCT_PCT = 0.98

SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2}

PATTERN_REGEX = {
    "email": r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$",
    "uuid": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    "url": r"^https?://\S+$",
}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


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


def _name_tokens(name: str) -> set[str]:
    """`totalAmount`, `total_amount`, `TOTAL-AMOUNTS` -> {'total', 'amount', ...}."""
    tokens = {t for t in _TOKEN_SPLIT.split(_CAMEL_BOUNDARY.sub("_", name).lower()) if t}
    return tokens | {t[:-1] for t in tokens if len(t) > 3 and t.endswith("s")}


def _num(v: Any) -> float | None:
    """Finite float, or None — profile JSON can carry None/str/bool in these slots."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _quantiles(col: dict[str, Any]) -> dict[str, float]:
    q = col.get("quantiles") or {}
    return {k: v for k in QUANTILE_KEYS if (v := _num(q.get(k))) is not None}


_INT_DTYPE = re.compile(r"\bint\b|integer|bigint|smallint|tinyint|int\d+|serial")


def _whole_number_column(col: dict[str, Any], lo: float, hi: float) -> bool:
    """Whether bounds for this column should be whole numbers.

    An integer column with a bound of -0.80331 reads as a machine artefact and
    costs the proposal its credibility. Quantiles alone can't decide it (pandas
    interpolates, so an int column's p01 comes back as 20.99000000000001) — the
    declared type, or wholly-integral evidence, does.
    """
    if not (float(lo).is_integer() and float(hi).is_integer()):
        return False
    if _INT_DTYPE.search(str(col.get("dtype") or "").lower()):
        return True
    return all(v.is_integer() for v in _quantiles(col).values())


def _round_out(v: float, *, up: bool, whole: bool = False) -> float | int:
    """Round a bound to ~5 significant digits, outward from the accepted interval
    (upper bounds up, lower bounds down), so tidying the number can never tighten a
    bound into firing on a value the derivation meant to accept. Also strips the
    float dust (111.00000000000001) that would otherwise land in an analyst's face.
    """
    if v == 0 or not math.isfinite(v):
        return 0 if v == 0 else v
    if whole:
        return math.ceil(v) if up else math.floor(v)
    digits = max(0, 4 - int(math.floor(math.log10(abs(v)))))
    factor = 10.0**digits
    scaled = v * factor
    rounded = (math.ceil(scaled) if up else math.floor(scaled)) / factor
    return int(rounded) if rounded.is_integer() and abs(rounded) < 1e15 else round(rounded, digits)


def _fences(col: dict[str, Any]) -> tuple[float | None, float | None]:
    """(lower, upper) robust fences from the sampled distribution, per side.

    The extreme quantiles say where the legitimate tail is; the headroom says how
    far beyond it a value stops looking like tail and starts looking like a defect.
    A side is None when the profile gives nothing to derive it from (no quantiles,
    or a degenerate constant column) — the caller then falls back to padding the
    observed range and says so.

    Every input is a quantile, deliberately. Backfilling the scale with stddev when
    the IQR is 0 looks like a harmless fallback and is not: on a zero-inflated column
    (a fee charged on 0.5% of rows) *every* stored quantile is 0, so the fence became
    ``0 + 3σ`` of the zero-dominated mixture — an upper bound sitting below the whole
    legitimate non-zero population, proposed at error severity with a rationale
    calling that ordinary data "a defect to catch". A point mass is exactly the
    degenerate case this returns None for; the padded observed range is the honest
    answer there. A flat bulk with a real tail (p95 10, p99 40) still gets a fence —
    that comes from the tail term, which needs no scale fallback.
    """
    q = _quantiles(col)
    if not all(k in q for k in ("0.01", "0.05", "0.25", "0.75", "0.95", "0.99")):
        return None, None
    iqr = max(q["0.75"] - q["0.25"], 0.0)
    hi_head = max(TAIL_HEADROOM * max(q["0.99"] - q["0.95"], 0.0), IQR_HEADROOM * iqr)
    lo_head = max(TAIL_HEADROOM * max(q["0.05"] - q["0.01"], 0.0), IQR_HEADROOM * iqr)
    return (
        q["0.01"] - lo_head if lo_head > 0 else None,
        q["0.99"] + hi_head if hi_head > 0 else None,
    )


def _negative_share(q: dict[str, float]) -> str:
    """Bracket how much of the sample sits below zero, straight from the quantiles."""
    below = [float(k) * 100 for k in q if q[k] < 0]
    if not below:
        return "sub-1%"
    above = [float(k) * 100 for k in q if q[k] >= 0]
    return f"{max(below):g}–{min(above):g}%" if above else "over-99%"


def _range_proposal(col: dict[str, Any]) -> dict[str, Any] | None:
    """The single range proposal for a numeric column (#256, #264).

    Lower bound: 0 when the column name reads as a non-negative measure and the
    profile does not contradict it; otherwise the robust lower fence, else padding.
    Upper bound: the robust upper fence when the observed max sits beyond it,
    otherwise the padded observed max. Every branch names its own basis in the
    rationale, because that text is what the analyst reviews.
    """
    name = col["name"]
    lo, hi = _num(col.get("min")), _num(col.get("max"))
    if lo is None or hi is None or hi <= lo:
        return None

    q = _quantiles(col)
    p01, p05, p99 = q.get("0.01"), q.get("0.05"), q.get("0.99")
    fence_lo, fence_hi = _fences(col)
    span = hi - lo
    pad = span * 0.5 if span else max(abs(hi), 1.0)
    padded_lo, padded_hi = lo - pad, hi + pad

    whole = _whole_number_column(col, lo, hi)
    tokens = _name_tokens(name)
    non_negative = bool(tokens & NON_NEGATIVE_HINTS) and not (tokens & SIGNED_HINTS)
    negatives_are_normal = p05 is not None and p05 < 0

    clauses: list[str] = []
    severity = "warn"

    if non_negative and not negatives_are_normal:
        bound_lo: float | int = 0
        severity = "error"
        if lo < 0:
            clauses.append(
                f"Min 0: the name reads as a non-negative measure and values below 0 are a "
                f"{_negative_share(q)} tail (observed min {lo:g}) — the check flags those rows "
                f"instead of accepting them as the floor."
            )
        else:
            clauses.append(
                f"Min 0: the name reads as a non-negative measure and nothing in the profile is "
                f"below 0 (observed min {lo:g})."
            )
    else:
        note = ""
        if non_negative and negatives_are_normal:
            note = (
                f" No zero floor assumed despite the name: negative values are normal here "
                f"({_negative_share(q)} of the sample, p05 {p05:g})."
            )
        if fence_lo is not None and fence_lo > padded_lo:
            bound_lo = _round_out(fence_lo, up=False, whole=whole)
            basis = f"p01 ({p01:g}) minus tail headroom"
            if lo < bound_lo:
                clause = (
                    f"Min {bound_lo:g}: {basis} — not the observed min {lo:g}, which falls below "
                    f"the robust fence and is treated as a defect to catch, not a valid bound."
                )
            else:
                clause = (
                    f"Min {bound_lo:g}: {basis}; tighter than padding the observed min {lo:g}, "
                    f"which no profiled value violates."
                )
            clauses.append(clause + note)
        else:
            bound_lo = _round_out(padded_lo, up=False, whole=whole)
            clauses.append(
                f"Min {bound_lo:g}: observed min {lo:g} padded by half the observed span; the "
                f"lower tail is smooth, so there is no anomaly to exclude." + note
            )

    if fence_hi is not None and fence_hi < padded_hi:
        bound_hi = _round_out(fence_hi, up=True, whole=whole)
        basis = f"p99 ({p99:g}) plus tail headroom"
        if hi > bound_hi:
            ratio = f" ({hi / p99:,.0f}× the p99)" if p99 and p99 > 0 else ""
            clauses.append(
                f"Max {bound_hi:g}: {basis} — not the observed max {hi:g}{ratio}, which is far "
                f"beyond the robust fence and is treated as a defect to catch, not a valid bound."
            )
        else:
            clauses.append(
                f"Max {bound_hi:g}: {basis}; tighter than padding the observed max {hi:g}, which "
                f"no profiled value violates."
            )
    else:
        bound_hi = _round_out(padded_hi, up=True, whole=whole)
        clauses.append(
            f"Max {bound_hi:g}: observed max {hi:g} padded by half the observed span; the upper "
            f"tail is smooth, so there is no anomaly to exclude."
        )

    if bound_lo >= bound_hi:  # unsatisfiable pair — every row would violate it
        return None
    return _proposal("range", name, {"min": bound_lo, "max": bound_hi}, severity, " ".join(clauses))


def _specificity(p: dict[str, Any]) -> int:
    return sum(1 for v in (p.get("params") or {}).values() if v not in (None, "", [], {}))


def _most_useful(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Most constraining params, then strongest severity, then emission order."""
    return max(group, key=lambda p: (_specificity(p), SEVERITY_RANK.get(p["severity"], 0)))


def _merge_ranges(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Intersect same-column range proposals: every bound survives, none is dropped."""
    mins = [m for p in group if (m := _num((p.get("params") or {}).get("min"))) is not None]
    maxs = [m for p in group if (m := _num((p.get("params") or {}).get("max"))) is not None]
    lo = max(mins) if mins else None
    hi = min(maxs) if maxs else None
    if lo is not None and hi is not None and lo >= hi:
        return _most_useful(group)  # the intersection is empty: every row would violate
    kept = (_most_useful(group).get("params") or {}).items()
    params = {k: v for k, v in kept if k not in ("min", "max")}
    if lo is not None:
        params["min"] = lo
    if hi is not None:
        params["max"] = hi
    rationales: list[str] = []
    for p in group:
        if p.get("rationale") and p["rationale"] not in rationales:
            rationales.append(p["rationale"])
    merged = dict(group[0])
    merged["params"] = params
    merged["severity"] = max(group, key=lambda p: SEVERITY_RANK.get(p["severity"], 0))["severity"]
    merged["rationale"] = " ".join(rationales)
    return merged


def _dedupe_most_useful(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse proposals that target the same (check_type, column) — see #264.

    The API keeps whichever proposal for a target it encounters first, so before
    this the *order* of the appends below decided which rule an analyst ever saw,
    and the loser vanished with no signal. Resolve it here, explicitly:

    * ``range`` proposals are **merged, not dropped**: the survivor is the
      intersection of the accepted intervals (tightest min, tightest max) carrying
      the strongest severity and both rationales, so no assertion is lost and the
      analyst can see where each bound came from. An empty intersection would flag
      every row, so that case falls back to keeping one proposal.
    * every other type keeps the **more specific** proposal — most constraining
      params, then strongest severity, then emission order as a stable tie-break.

    Position in the returned list is that of the group's first member, so the
    overall ordering of the proposal wall is unchanged.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for p in proposals:
        groups.setdefault((p["check_type"], p.get("column_name") or ""), []).append(p)
    out: list[dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) == 1:
            out.append(group[0])
        elif key[0] == "range":
            out.append(_merge_ranges(group))
        else:
            out.append(_most_useful(group))
    return out


def _ml_feature_candidates(profile: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """The numeric columns an ``ml_outlier`` check would actually use as features.

    This mirrors ``check_types._ml_feature_columns``, deliberately: that is the
    predicate the runtime applies, and it refuses to run below two surviving
    features (#263). Gating the proposal on the *unfiltered* numeric count therefore
    proposed checks that could only ever no-op, and listed the dropped id columns in
    the rationale as though they were the basis.

    Both cardinality rules are conditioned on an integral declared type: a surrogate
    key is an integer, while a float measurement that happens to be all-distinct on
    the sample (a price, a sensor reading) is a genuine feature and must survive
    being named a pk candidate. That integrality test is spelled the same way as the
    runtime's (``"int" in dtype``) rather than via ``_INT_DTYPE``, because agreeing
    with ``check_types`` matters more here than the regex being tidier: the proposal
    pins ``columns``, and an explicit list is taken verbatim, so wherever the two
    disagree it is this answer that silently wins.
    """
    pk_candidates = {str(c).lower() for c in facts.get("pk_candidates") or []}
    keep: list[str] = []
    for col in profile.get("columns", []):
        if col["kind"] != "numeric":
            continue
        name = str(col["name"])
        dtype = str(col.get("dtype") or "").lower()
        integral = "int" in dtype
        if name.lower() in pk_candidates and integral:
            continue
        if ml.looks_like_identifier_name(name):
            continue
        if (_num(col.get("distinct_pct")) or 0.0) >= ML_ID_DISTINCT_PCT and integral:
            continue  # near-unique integer with an ordinary name: an unnamed surrogate key
        keep.append(name)
    return keep


def _profile_contract_columns(profile: dict[str, Any]) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    for col in profile.get("columns", []):
        expected: dict[str, Any] = {"name": col["name"]}
        if col.get("dtype") is not None:
            expected["dtype"] = col["dtype"]
        if col.get("nullable") is not None:
            expected["nullable"] = col["nullable"]
        columns.append(expected)
    return columns


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
            proposal = _range_proposal(col)
            if proposal is not None:
                out.append(proposal)

        if kind == "string":
            # strongest signal first: a column matching two formats should be pinned
            # to the one more of its values actually satisfy (#264).
            patterns = sorted((col.get("patterns") or {}).items(), key=lambda kv: -kv[1])
            for pat, ratio in patterns:
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
            fallback_hours = sla or 48
            out.append(
                _proposal(
                    "freshness",
                    t["name"],
                    {
                        "strategy": "adaptive",
                        "default_max_age_hours": fallback_hours,
                        "min_history": 3,
                        "lookback_runs": 14,
                        "multiplier": 2.0,
                        "grace_hours": 1.0,
                    },
                    "error" if sla else "warn",
                    ("Freshness SLA from table knowledge" if sla else "Temporal column — default 48h SLA"),
                    schedule_minutes=360,
                )
            )
            break  # one freshness check on the best candidate

    # Schema contract: pin the profiled columns as the expected table contract.
    contract_columns = _profile_contract_columns(profile)
    if contract_columns:
        out.append(
            _proposal(
                "schema_contract",
                None,
                {"expected_columns": contract_columns, "allow_additive": True, "case_sensitive": False},
                "warn",
                "Validate the current table columns against the profiled schema contract",
                schedule_minutes=360,
            )
        )

    # schema-change monitor (#101): cheap, valuable on every dataset — a dropped
    # or retyped column is a classic silent break. Default baseline = previous run.
    out.append(
        _proposal(
            "schema_change", None, {"baseline": "previous"}, "warn",
            "Alert when columns are added/removed/retyped vs the previous run",
            schedule_minutes=360,
        )
    )

    if rows >= 100:
        out.append(
            _proposal(
                "row_count_min", None, {"min_rows": max(1, rows // 2)}, "error",
                f"Table had {rows} rows when profiled; alert if it halves",
            )
        )
        out.append(
            _proposal(
                "row_count_anomaly",
                None,
                {"strategy": "adaptive", "lookback_runs": 14, "min_history": 5, "multiplier": 3.5},
                "warn",
                "Detect unusual row-count jumps/drops against a robust recent baseline",
            )
        )

    ml_cols = _ml_feature_candidates(profile, facts)
    if rows >= 500 and len(ml_cols) >= 2:
        shown = ", ".join(ml_cols[:6]) + ("…" if len(ml_cols) > 6 else "")
        out.append(
            _proposal(
                "ml_outlier", None, {"contamination": 0.005, "columns": ml_cols}, "info",
                f"IsolationForest across {len(ml_cols)} numeric measure column"
                f"{'s' if len(ml_cols) != 1 else ''} ({shown}). Key and identifier-like "
                "columns are excluded: a rare id is not a data-quality defect.",
            )
        )

    # distribution drift: PSI vs the profiling baseline. Cover the 3 highest-variance
    # numeric columns (most likely to shift meaningfully) plus low-cardinality
    # categoricals (a vanished/new category is a classic silent break).
    if rows >= 500:
        scored = [
            (abs(float(c["stddev"])), c["name"])
            for c in profile.get("columns", [])
            if c["kind"] == "numeric" and isinstance(c.get("stddev"), (int, float)) and c["stddev"]
        ]
        for _var, name in sorted(scored, reverse=True)[:3]:
            out.append(
                _proposal(
                    "distribution_drift", name, {"method": "psi", "threshold": 0.2}, "info",
                    "Alert if this numeric column's distribution drifts from the profiling baseline (PSI)",
                )
            )
        for col in profile.get("columns", []):
            if (
                col["kind"] == "string"
                and 0 < col["distinct_count"] <= 20
                and col.get("top_values")
                and not any(h in col["name"].lower() for h in ID_HINTS)
            ):
                out.append(
                    _proposal(
                        "distribution_drift", col["name"], {"method": "psi", "threshold": 0.2}, "info",
                        f"Alert if the category mix of {col['name']} drifts from the baseline (PSI)",
                    )
                )
    return _dedupe_most_useful(out)
