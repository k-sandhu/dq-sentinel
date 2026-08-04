from app.connectors.sa import Connector
from app.core.check_types import CHECK_TYPES
from app.core.generator import PATTERN_REGEX, heuristic_proposals
from app.core.profiler import profile_dataset


def test_heuristics_from_real_profile(source_db):
    connector = Connector(source_db)
    profile = profile_dataset(connector, "people", None, sample_rows=10_000)
    proposals = heuristic_proposals(profile, knowledge={"freshness_sla_hours": 24})

    by_type: dict[str, list] = {}
    for p in proposals:
        by_type.setdefault(p["check_type"], []).append(p)

    assert all(p["check_type"] in CHECK_TYPES for p in proposals)
    # id is a fully-distinct pk candidate -> not_null + unique
    assert any(p["column_name"] == "id" for p in by_type.get("not_null", []))
    assert any(p["column_name"] == "id" for p in by_type.get("unique", []))
    # numeric columns get range checks
    assert any(p["column_name"] == "age" for p in by_type.get("range", []))
    # temporal column + SLA from knowledge -> freshness with that SLA
    fresh = by_type.get("freshness", [])
    assert fresh and fresh[0]["params"]["strategy"] == "adaptive"
    assert fresh[0]["params"]["default_max_age_hours"] == 24
    assert fresh[0]["severity"] == "error"
    # table-level guards always present
    contract = by_type.get("schema_contract", [])
    assert contract and contract[0]["params"]["allow_additive"] is True
    assert {c["name"] for c in contract[0]["params"]["expected_columns"]} >= {"id", "email"}
    assert "row_count_min" in by_type
    assert "row_count_anomaly" in by_type
    assert by_type["row_count_anomaly"][0]["params"]["strategy"] == "adaptive"
    # every proposal has a schedule and rationale
    assert all(p["schedule_expr"] and p["rationale"] for p in proposals)
    # #256: the fixture plants one age=999 among 18..65. The proposed bound must not
    # accept it — a bound derived from the observed max certifies the planted defect.
    age_ranges = [p for p in by_type["range"] if p["column_name"] == "age"]
    assert len(age_ranges) == 1, "one range proposal per column (#264)"
    assert age_ranges[0]["params"]["max"] < 999
    assert age_ranges[0]["params"]["max"] > 70  # but still above every legitimate age
    # an INTEGER column gets whole-number bounds, not -0.80331-style machine artefacts
    assert isinstance(age_ranges[0]["params"]["max"], int)
    assert isinstance(age_ranges[0]["params"]["min"], int)


def test_accepted_values_only_for_small_domains():
    profile = {
        "row_count": 1000,
        "sampled_rows": 1000,
        "table_facts": {},
        "columns": [
            {
                "name": "status", "dtype": "TEXT", "kind": "string",
                "null_count": 0, "null_pct": 0.0, "distinct_count": 3, "distinct_pct": 0.003,
                "top_values": [
                    {"value": "a", "count": 600},
                    {"value": "b", "count": 300},
                    {"value": "c", "count": 100},
                ],
                "patterns": {}, "quantiles": {},
            }
        ],
    }
    proposals = heuristic_proposals(profile)
    accepted = [p for p in proposals if p["check_type"] == "accepted_values"]
    assert len(accepted) == 1
    assert set(accepted[0]["params"]["values"]) == {"a", "b", "c"}


def test_distribution_drift_proposals():
    def numeric(name, std):
        return {
            "name": name, "dtype": "REAL", "kind": "numeric", "null_count": 0, "null_pct": 0.0,
            "distinct_count": 900, "distinct_pct": 0.9, "stddev": std, "quantiles": {"0.5": 1.0},
            "patterns": {}, "top_values": [], "min": 0, "max": 10,
        }

    profile = {
        "row_count": 1000, "sampled_rows": 1000, "table_facts": {},
        "columns": [
            numeric("a", 5.0), numeric("b", 50.0), numeric("c", 0.5), numeric("d", 500.0),
            {
                "name": "region", "dtype": "TEXT", "kind": "string", "null_count": 0,
                "null_pct": 0.0, "distinct_count": 4, "distinct_pct": 0.004,
                "top_values": [{"value": "us", "count": 700}, {"value": "eu", "count": 300}],
                "patterns": {}, "quantiles": {},
            },
        ],
    }
    drift = [p for p in heuristic_proposals(profile) if p["check_type"] == "distribution_drift"]
    cols = {p["column_name"] for p in drift}
    # top-3 variance numerics (d, b, a) — not the low-variance 'c'
    assert {"d", "b", "a"} <= cols and "c" not in cols
    # low-cardinality categorical covered too
    assert "region" in cols
    assert all(p["severity"] == "info" and p["params"]["method"] == "psi" for p in drift)


# --- #256 / #264: range bounds must not be derived from the anomalies themselves ---

def _numeric_col(name, *, lo, hi, quantiles, stddev, distinct=5000):
    return {
        "name": name, "dtype": "REAL", "kind": "numeric",
        "null_count": 0, "null_pct": 0.0, "distinct_count": distinct, "distinct_pct": 0.05,
        "min": lo, "max": hi, "mean": quantiles["0.5"], "stddev": stddev,
        "quantiles": quantiles, "patterns": {}, "top_values": [],
    }


def _profile(*columns, rows=100_000):
    return {"row_count": rows, "sampled_rows": 50_000, "table_facts": {}, "columns": list(columns)}


# Shapes taken from the NYC-taxi profile in #256: a small negative tail (refund/void
# rows) and a max orders of magnitude above the p99.
FARE_Q = {"0.01": -3.0, "0.05": 5.1, "0.25": 8.6, "0.5": 12.1, "0.75": 19.8, "0.95": 52.0, "0.99": 70.0}
DIST_Q = {"0.01": 0.3, "0.05": 0.55, "0.25": 1.06, "0.5": 1.77, "0.75": 3.3, "0.95": 9.6, "0.99": 18.5}


def _ranges(profile):
    return {
        p["column_name"]: p
        for p in heuristic_proposals(profile)
        if p["check_type"] == "range"
    }


def test_range_bounds_exclude_planted_anomalies():
    """A bound derived from the observed min/max certifies the dirt as valid (#256)."""
    profile = _profile(
        _numeric_col("fare_amount", lo=-899.0, hi=5000.0, quantiles=FARE_Q, stddev=25.0),
        _numeric_col("trip_distance", lo=0.0, hi=312722.3, quantiles=DIST_Q, stddev=100.0),
    )
    ranges = _ranges(profile)

    fare = ranges["fare_amount"]
    assert fare["params"]["min"] == 0, "a negative fare must violate, not define, the bound"
    assert 70.0 < fare["params"]["max"] < 5000.0, "must clear the p99 but exclude the 5000 outlier"
    assert fare["severity"] == "error"
    # rationale honesty: the bound is not the observed range, and must not pretend to be
    assert "p99" in fare["rationale"]
    assert "5000" in fare["rationale"], "the excluded observed max belongs in the rationale"
    assert "-899" in fare["rationale"]
    assert "observed range" not in fare["rationale"].lower()

    dist = ranges["trip_distance"]
    assert dist["params"]["min"] == 0
    assert 18.5 < dist["params"]["max"] < 1000.0, "312722 mi must violate the proposed bound"


def test_range_keeps_padded_observed_bounds_when_the_tail_is_smooth():
    """No anomaly to exclude -> keep the padded observed range and say so."""
    smooth = {"0.01": 50.1, "0.05": 50.5, "0.25": 52.0, "0.5": 54.5, "0.75": 57.0,
              "0.95": 58.6, "0.99": 58.9}
    profile = _profile(_numeric_col("score", lo=50.0, hi=59.0, quantiles=smooth, stddev=2.9))
    score = _ranges(profile)["score"]
    assert score["params"]["max"] >= 59.0, "must not fire on clean profiled data"
    assert score["params"]["min"] <= 50.0
    assert score["severity"] == "warn"
    assert "observed" in score["rationale"].lower()


def test_no_zero_floor_when_negatives_are_normal_for_the_column():
    """A money-ish name is a prior, not a licence to invent a floor the data denies."""
    refund_q = {"0.01": -300.0, "0.05": -150.0, "0.25": -40.0, "0.5": 5.0, "0.75": 60.0,
                "0.95": 200.0, "0.99": 300.0}
    signed_q = {"0.01": 1.0, "0.05": 2.0, "0.25": 4.0, "0.5": 8.0, "0.75": 16.0,
                "0.95": 32.0, "0.99": 48.0}
    profile = _profile(
        # negatives are ~25% of the distribution -> structural, not defects
        _numeric_col("refund_amount", lo=-500.0, hi=500.0, quantiles=refund_q, stddev=100.0),
        # all-positive sample, but the name says this measure is signed by nature
        _numeric_col("net_amount", lo=1.0, hi=60.0, quantiles=signed_q, stddev=12.0),
    )
    ranges = _ranges(profile)
    assert ranges["refund_amount"]["params"]["min"] < 0
    assert "negative" in ranges["refund_amount"]["rationale"].lower()
    assert ranges["net_amount"]["params"]["min"] != 0
    assert ranges["net_amount"]["severity"] == "warn"


def test_one_range_proposal_per_column_carries_the_tighter_bound():
    """#264: two proposals for (range, col) let API dedup drop the useful one."""
    profile = _profile(
        _numeric_col("trip_distance", lo=0.0, hi=312722.3, quantiles=DIST_Q, stddev=100.0),
    )
    ranges = [p for p in heuristic_proposals(profile) if p["check_type"] == "range"]
    assert len(ranges) == 1
    assert ranges[0]["params"]["max"] < 312722.3


def test_strongest_pattern_survives_when_a_column_matches_two_formats():
    """#264 for regex_match: the weaker signal must not win on emission order."""
    profile = _profile(
        {
            "name": "link", "dtype": "TEXT", "kind": "string", "null_count": 0, "null_pct": 0.0,
            "distinct_count": 900, "distinct_pct": 0.9, "quantiles": {}, "top_values": [],
            # deliberately weakest-first, as the profiler's dict order may well be
            "patterns": {"url": 0.96, "email": 1.0},
        },
        rows=1000,
    )
    regexes = [p for p in heuristic_proposals(profile) if p["check_type"] == "regex_match"]
    assert len(regexes) == 1
    assert regexes[0]["params"]["pattern"] == PATTERN_REGEX["email"]
    assert regexes[0]["severity"] == "error"


def test_dedupe_merges_range_bounds_instead_of_dropping_one():
    """Same-(type,column) ranges intersect, so neither assertion is silently lost."""
    from app.core.generator import _dedupe_most_useful

    wide = {
        "check_type": "range", "column_name": "x", "params": {"min": -100.0, "max": 500.0},
        "severity": "warn", "rationale": "padded observed range", "schedule_kind": "interval",
        "schedule_expr": "1440",
    }
    tight = {**wide, "params": {"max": 60.0}, "severity": "error", "rationale": "p99 fence"}
    merged = _dedupe_most_useful([wide, tight])
    assert len(merged) == 1
    assert merged[0]["params"] == {"min": -100.0, "max": 60.0}
    assert merged[0]["severity"] == "error"
    assert "padded observed range" in merged[0]["rationale"]
    assert "p99 fence" in merged[0]["rationale"]

    # an empty intersection would flag every row — keep one usable proposal instead
    impossible = {**wide, "params": {"min": 900.0}, "rationale": "min above the other max"}
    kept = _dedupe_most_useful([wide, impossible])
    assert len(kept) == 1
    lo, hi = kept[0]["params"].get("min"), kept[0]["params"].get("max")
    assert lo is None or hi is None or lo < hi
