from app.connectors.sa import Connector
from app.core.generator import heuristic_proposals
from app.core.profiler import profile_dataset


def test_heuristics_from_real_profile(source_db):
    connector = Connector(source_db)
    profile = profile_dataset(connector, "people", None, sample_rows=10_000)
    proposals = heuristic_proposals(profile, knowledge={"freshness_sla_hours": 24})

    by_type: dict[str, list] = {}
    for p in proposals:
        by_type.setdefault(p["check_type"], []).append(p)

    # id is a fully-distinct pk candidate -> not_null + unique
    assert any(p["column_name"] == "id" for p in by_type.get("not_null", []))
    assert any(p["column_name"] == "id" for p in by_type.get("unique", []))
    # numeric columns get range checks
    assert any(p["column_name"] == "age" for p in by_type.get("range", []))
    # temporal column + SLA from knowledge -> freshness with that SLA
    fresh = by_type.get("freshness", [])
    assert fresh and fresh[0]["params"]["max_age_hours"] == 24
    assert fresh[0]["severity"] == "error"
    # table-level guards always present
    assert "row_count_min" in by_type
    assert "row_count_anomaly" in by_type
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
