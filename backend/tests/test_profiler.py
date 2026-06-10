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
