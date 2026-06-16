from app.connectors.sa import Connector
from app.core.check_types import CHECK_TYPES
from app.core.generator import heuristic_proposals
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
