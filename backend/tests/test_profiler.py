from app.connectors.sa import Connector
from app.core.profiler import profile_dataset, summarize_profile_for_llm
from tests.conftest import NULL_EMAILS, SOURCE_ROWS


def test_profile_people(source_db):
    connector = Connector(source_db)
    profile = profile_dataset(connector, "people", None, sample_rows=10_000)

    assert profile["row_count"] == SOURCE_ROWS
    cols = {c["name"]: c for c in profile["columns"]}

    assert cols["id"]["null_count"] == 0
    assert cols["id"]["distinct_count"] == SOURCE_ROWS
    assert "id" in profile["table_facts"]["pk_candidates"]

    email = cols["email"]
    assert email["null_count"] == NULL_EMAILS
    assert email["kind"] == "string"
    assert email["patterns"].get("email", 0) > 0.9

    age = cols["age"]
    assert age["kind"] == "numeric"
    assert age["max"] == 999
    assert age["quantiles"]["0.5"] is not None

    status = cols["status"]
    values = {t["value"] for t in status["top_values"]}
    assert {"active", "inactive"} <= values

    temporal = [t["name"] for t in profile["table_facts"]["temporal_columns"]]
    assert "created_at" in temporal


def test_summary_redacts_pii(source_db):
    connector = Connector(source_db)
    profile = profile_dataset(connector, "people", None, sample_rows=1000)
    text = summarize_profile_for_llm(profile, pii_columns=["email"])
    assert "[values redacted: PII]" in text
    assert "user10@example.com" not in text


def test_jsonable_rejects_non_finite_decimals():
    """PostgreSQL `numeric` (and 14+ `Infinity`) can hold non-finite values, and
    `json.dumps` renders them as the bare tokens NaN/Infinity, which are invalid
    JSON. Those land in `ExceptionRecord.row_data` (a JSON column) via
    `_truncate_row`, so an unguarded Decimal fails the INSERT at commit — outside
    runner.py's error handling, meaning the check 500s with no run row at all.

    No DuckDB-backed test can reach this branch (its DECIMAL cannot hold NaN),
    so it is pinned directly. Regression guard for the #271 review finding.
    """
    import json
    from decimal import Decimal

    from app.core.profiler import jsonable

    assert jsonable(Decimal("NaN")) is None
    assert jsonable(Decimal("Infinity")) is None
    assert jsonable(Decimal("-Infinity")) is None
    # Finite decimals must still round-trip as numbers.
    assert jsonable(Decimal("12.50")) == 12.5
    # And the whole point: the result must be serializable as valid JSON.
    assert json.dumps({"n": jsonable(Decimal("NaN"))}) == '{"n": null}'
