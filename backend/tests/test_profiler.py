import tempfile
from pathlib import Path

import pytest

from app.connectors.sa import RANDOM_SORT_MAX_ROWS, SAMPLE_SEED, Connector
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


# --------------------------------------------------- #269: the sample must be a sample
# `fetch_df` is a bare `LIMIT n` — the first rows the engine reaches. On an append-only
# fact table those rows are one contiguous, temporally clustered slice, so mean/stddev/
# p1..p99 described the OLDEST slice while being served next to the exact full-table
# row count. Verified on the 2.96M-row taxi table: every datetime sample_value was
# 2024-01-01T00:xx and temporal_columns[0].max read 2024-01-01T17:01 against a real max
# of 2024-02-01. These build the same shape — values that trend with physical row order,
# which is precisely what makes a positional read visible as bias.

ORDERED_ROWS = 120_000
ORDERED_SAMPLE = 20_000
# v = row index, so the population mean/median is (ORDERED_ROWS - 1) / 2 while the
# first ORDERED_SAMPLE rows average (ORDERED_SAMPLE - 1) / 2 — 6x lower.
ORDERED_TRUE_MEAN = (ORDERED_ROWS - 1) / 2
ORDERED_HEAD_MEAN = (ORDERED_SAMPLE - 1) / 2


@pytest.fixture(scope="module")
def ordered_tmp() -> Path:
    # pytest's default basetemp is unwritable on this machine (OneDrive/Temp ACLs);
    # mirror conftest's own mkdtemp approach instead.
    return Path(tempfile.mkdtemp(prefix="dqsentinel-ordered-"))


@pytest.fixture(scope="module")
def ordered_duckdb(ordered_tmp) -> str:
    """A large table written in time order: `occurred_at` and `v` both climb with the
    physical row order, exactly like a partitioned/append-only fact table."""
    import duckdb

    path = ordered_tmp / "ordered.duckdb"
    con = duckdb.connect(str(path))  # writer closed before any connector opens it
    try:
        con.execute(
            "CREATE TABLE events AS SELECT "
            "TIMESTAMP '2024-01-01 00:00:00' + INTERVAL (i) MINUTE AS occurred_at, "
            "CAST(i AS DOUBLE) AS v "
            f"FROM range({ORDERED_ROWS}) t(i)"
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return f"duckdb:///{path.as_posix()}"


@pytest.fixture(scope="module")
def ordered_profile(ordered_duckdb) -> dict:
    connector = Connector(ordered_duckdb)
    try:
        return profile_dataset(connector, "events", None, sample_rows=ORDERED_SAMPLE)
    finally:
        connector.engine.dispose()


def test_profile_sample_is_drawn_from_the_whole_table_not_its_head(ordered_profile):
    sampling = ordered_profile["table_facts"]["sampling"]
    assert sampling["method"] == "reservoir"  # DuckDB's native uniform sampler
    assert sampling["representative"] is True
    assert sampling["sampled"] is True
    assert sampling["rows"] == ORDERED_SAMPLE
    assert sampling["row_count"] == ORDERED_ROWS

    v = next(c for c in ordered_profile["columns"] if c["name"] == "v")
    # The population mean is 59999.5; the first 20k rows average 9999.5. A 20k uniform
    # sample of 0..119999 has a standard error of ~224, so this band is ~20 sigma wide
    # on the correct side and unreachable from the biased one.
    assert abs(v["mean"] - ORDERED_TRUE_MEAN) < 0.05 * ORDERED_TRUE_MEAN
    assert v["mean"] > 5 * ORDERED_HEAD_MEAN
    assert v["quantiles"]["0.99"] > 0.9 * (ORDERED_ROWS - 1)


def test_profile_temporal_max_is_the_exact_sql_max(ordered_duckdb, ordered_profile):
    """table_facts.temporal_columns[].max must reuse the exact full-table MAX() the
    profiler already computes, not the max of the sample."""
    connector = Connector(ordered_duckdb)
    try:
        exact = connector.scalar('SELECT MAX("occurred_at") FROM "events"')
    finally:
        connector.engine.dispose()

    entry = next(
        t for t in ordered_profile["table_facts"]["temporal_columns"] if t["name"] == "occurred_at"
    )
    assert entry["max"] == exact.isoformat()
    # And it agrees with the column's own exact max, which comes from the same SQL.
    occurred = next(c for c in ordered_profile["columns"] if c["name"] == "occurred_at")
    assert entry["max"] == occurred["max"]


def test_profile_labels_which_stats_came_from_the_sample(ordered_profile):
    sampling = ordered_profile["table_facts"]["sampling"]
    assert {"mean", "stddev", "quantiles", "sample_values"} <= set(sampling["sample_stats"])
    assert {"row_count", "distinct_count", "min", "max", "top_values"} <= set(
        sampling["population_stats"]
    )


def test_profile_sampling_is_reproducible_on_unchanged_data(ordered_duckdb, ordered_profile):
    """Two profiles of an unchanged table must not produce two different baselines —
    that alone would show up as distribution drift."""
    connector = Connector(ordered_duckdb)
    try:
        again = profile_dataset(connector, "events", None, sample_rows=ORDERED_SAMPLE)
    finally:
        connector.engine.dispose()
    assert again["table_facts"]["sampling"]["seed"] == SAMPLE_SEED
    first = next(c for c in ordered_profile["columns"] if c["name"] == "v")
    second = next(c for c in again["columns"] if c["name"] == "v")
    assert second["quantiles"] == first["quantiles"]
    assert second["mean"] == first["mean"]


def test_profile_of_a_table_that_fits_is_not_reported_as_sampled(source_db):
    connector = Connector(source_db)
    sampling = profile_dataset(connector, "people", None, sample_rows=10_000)["table_facts"][
        "sampling"
    ]
    assert sampling["method"] == "full"
    assert sampling["sampled"] is False
    assert sampling["representative"] is True
    assert sampling["rows"] == SOURCE_ROWS


def test_profile_falls_back_to_random_sort_without_a_native_sampler(source_db):
    """SQLite has no TABLESAMPLE, so a table bigger than the cap gets ORDER BY RANDOM()
    — representative, but honestly reported as not reproducible."""
    connector = Connector(source_db)
    profile = profile_dataset(connector, "people", None, sample_rows=50)
    sampling = profile["table_facts"]["sampling"]
    assert sampling["method"] == "random_sort"
    assert sampling["representative"] is True
    assert sampling["reproducible"] is False
    assert sampling["seed"] is None
    assert sampling["sampled"] is True

    # ids run 1..200; the first 50 average 25.5, a random 50 average 100.5 with a
    # standard error of ~7, so anything above 60 is unreachable from the head read.
    ids = next(c for c in profile["columns"] if c["name"] == "id")
    assert ids["mean"] > 60


def test_sample_df_refuses_a_full_sort_on_a_huge_table_and_says_so(source_db):
    """The fallback is a top-N sort, so it must not be what a 100M-row table gets by
    default. Past the ceiling the read is positional again — and `representative` says
    so, which is the whole point of carrying the flag."""
    connector = Connector(source_db)
    sample = connector.sample_df(
        "*", connector.table_ref("people", None), 50, row_count=RANDOM_SORT_MAX_ROWS + 1
    )
    assert sample.method == "head"
    assert sample.representative is False
    assert sample.truncated is True
    assert len(sample.df) == 50


def test_summary_tells_the_model_the_stats_are_sampled(source_db):
    connector = Connector(source_db)
    profile = profile_dataset(connector, "people", None, sample_rows=50)
    text = summarize_profile_for_llm(profile)
    assert "counts and min/max are exact" in text
    assert "random sample" in text


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
