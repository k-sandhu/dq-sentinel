import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.connectors.dialects import nan_predicate_builder
from app.connectors.sa import Connector
from app.core.check_types import (
    CheckContext,
    _categorical_drift,
    _nan_violation_predicate,
    _numeric_drift,
    _prefers_value_mix,
    _quantile_bins,
    run_check_type,
    validate_check,
)
from tests.conftest import BAD_EMAILS, BAD_STATUS, HUGE_AGE, NULL_EMAILS, SOURCE_ROWS


@pytest.fixture(scope="module")
def ctx_factory(source_db):
    connector = Connector(source_db)

    def make(column=None, params=None):
        return CheckContext(
            connector=connector, table="people", schema=None, column=column, params=params or {}
        )

    return make


def test_not_null(ctx_factory):
    r = run_check_type(ctx_factory("email"), "not_null")
    assert r.violation_count == NULL_EMAILS
    assert len(r.sample_rows) == NULL_EMAILS
    assert all(row["email"] is None for row in r.sample_rows)


def test_unique(ctx_factory):
    r = run_check_type(ctx_factory("email"), "unique")
    assert r.metrics["duplicate_groups"] == 1
    assert r.metrics["duplicated_rows"] == 2
    assert r.violation_count == 1  # one surplus duplicate row
    assert len(r.sample_rows) == 2


def test_accepted_values(ctx_factory):
    r = run_check_type(
        ctx_factory("status", {"values": ["active", "inactive"]}), "accepted_values"
    )
    assert r.violation_count == BAD_STATUS
    assert r.sample_rows[0]["status"] == "x"


def test_accepted_values_binds_special_chars(ctx_factory):
    # A value containing a quote/backslash must not break the SQL or bypass the guard
    # (values are bound, not inlined). The odd extra value simply matches nothing.
    r = run_check_type(
        ctx_factory("status", {"values": ["active", "inactive", "o'brien\\"]}),
        "accepted_values",
    )
    assert r.violation_count == BAD_STATUS


def test_validate_accepted_values_requires_list():
    # a bare comma string used to iterate into IN ('a', ',', 'b', ...) — reject it
    with pytest.raises(ValueError):
        validate_check("accepted_values", "status", {"values": "active,inactive"})


def test_range(ctx_factory):
    r = run_check_type(ctx_factory("age", {"min": 0, "max": 120}), "range")
    assert r.violation_count == HUGE_AGE
    assert r.sample_rows[0]["age"] == 999


# ------------------------------------------------------------------ range + NaN (#271)
# NaN is not NULL and no bound comparison is true for it, so `col IS NOT NULL AND
# col < :min` used to give a min-only range check a clean bill of health on a NaN.
# These run against a real DuckDB file because the bug is engine semantics, not logic.


@pytest.fixture(scope="module")
def nan_tmp() -> Path:
    # Same reason as drift_tmp below: pytest's default basetemp is unwritable here.
    return Path(tempfile.mkdtemp(prefix="dqsentinel-nan-"))


@pytest.fixture(scope="module")
def nan_ctx_factory(nan_tmp):
    """CheckContext factory over a DuckDB table holding NaN, NULL, in- and out-of-range
    values, plus a DATE column (range checks are used for date bounds too)."""
    import duckdb

    db = nan_tmp / "nan.duckdb"
    con = duckdb.connect(str(db))  # writer fully closed before the connector opens it
    try:
        con.execute("CREATE TABLE metrics (label VARCHAR, amount DOUBLE, event_date DATE)")
        con.execute(
            "INSERT INTO metrics VALUES "
            "('ok', 5.0, DATE '2024-01-01'), "
            "('nan', CAST('nan' AS DOUBLE), DATE '2024-01-02'), "
            "('below', -1.0, DATE '2024-01-03'), "
            "('null', NULL, DATE '2024-01-04'), "
            "('above', 200.0, DATE '2019-01-01')"
        )
    finally:
        con.close()
    connector = Connector(f"duckdb:///{db.as_posix()}")

    def make(column=None, params=None):
        return CheckContext(
            connector=connector, table="metrics", schema=None, column=column, params=params or {}
        )

    yield make
    connector.engine.dispose()


def _labels(result) -> set[str]:
    return {row["label"] for row in result.sample_rows}


def test_range_min_only_flags_nan(nan_ctx_factory):
    """The #271 case: with only a lower bound, NaN used to pass as in-range."""
    r = run_check_type(nan_ctx_factory("amount", {"min": 0}), "range")
    assert _labels(r) == {"nan", "below"}
    assert r.violation_count == 2


def test_range_max_only_flags_nan(nan_ctx_factory):
    # A max bound already caught NaN (it sorts above every value on DuckDB), so this
    # pins that the fix did not change the answer here.
    r = run_check_type(nan_ctx_factory("amount", {"max": 120}), "range")
    assert _labels(r) == {"nan", "above"}
    assert r.violation_count == 2


def test_range_two_sided_flags_nan(nan_ctx_factory):
    r = run_check_type(nan_ctx_factory("amount", {"min": 0, "max": 120}), "range")
    assert _labels(r) == {"nan", "below", "above"}
    assert r.violation_count == 3


@pytest.mark.parametrize("params", [{"min": 0}, {"max": 120}, {"min": 0, "max": 120}])
def test_range_no_false_positives_on_valid_or_null_rows(nan_ctx_factory, params):
    """The in-range value and the NULL must never be flagged, whatever the bounds."""
    r = run_check_type(nan_ctx_factory("amount", params), "range")
    assert "ok" not in _labels(r)
    assert "null" not in _labels(r)


def test_range_nan_reason_says_it_is_not_a_number(nan_ctx_factory):
    """NaN has no JSON representation, so the row reads `amount = None`; the reason
    must say what is actually wrong instead of `'nan' outside [0, inf]`."""
    r = run_check_type(nan_ctx_factory("amount", {"min": 0}), "range")
    reasons = dict(zip([row["label"] for row in r.sample_rows], r.reasons, strict=True))
    assert "not a finite number" in reasons["nan"]
    assert "-1.0 outside [0, inf]" in reasons["below"]  # ordinary rows keep their wording


def test_range_on_date_column_is_unaffected(nan_ctx_factory):
    """Regression guard: `range` is used for date bounds too, and an ungated NaN
    predicate is a binder error there (`isnan(DATE)` on DuckDB, `date = 'NaN'` on
    PostgreSQL) — it would turn a working check into a failing one."""
    ctx = nan_ctx_factory("event_date", {"min": "2024-01-01"})
    assert _nan_violation_predicate(ctx) is None  # gated off by column type
    r = run_check_type(ctx, "range")
    assert _labels(r) == {"above"}  # only the 2019 row
    assert r.violation_count == 1


def test_range_nan_predicate_gating(nan_ctx_factory):
    ctx = nan_ctx_factory("amount", {})
    assert _nan_violation_predicate(ctx) == f"isnan({ctx.col})"  # DOUBLE
    assert _nan_violation_predicate(nan_ctx_factory("label", {})) is None  # VARCHAR


def test_ieee_nan_inequality_would_not_have_worked(nan_ctx_factory):
    """Why the fix is a per-dialect predicate and not `col != col`: DuckDB (like
    PostgreSQL) deviates from IEEE — NaN equals itself and sorts above everything —
    so the obvious one-liner matches nothing on the engine this bug was found on."""
    connector = nan_ctx_factory().connector
    assert connector.scalar("SELECT COUNT(*) FROM metrics WHERE amount != amount") == 0
    assert connector.scalar("SELECT COUNT(*) FROM metrics WHERE isnan(amount)") == 1


def test_nan_predicate_registry_shapes():
    """The per-dialect spellings, including the engines with no live coverage here.
    Every one must be gated to the types that engine can hold a NaN in."""
    duckdb_nan = nan_predicate_builder("duckdb")
    assert duckdb_nan('"x"', "FLOAT") == 'isnan("x")'
    assert duckdb_nan('"x"', "NUMERIC(10, 2)") is None  # DuckDB DECIMAL is exact
    assert duckdb_nan('"x"', "DATE") is None

    pg_nan = nan_predicate_builder("postgresql")
    assert pg_nan('"x"', "DOUBLE PRECISION") == "\"x\" = 'NaN'"
    assert pg_nan('"x"', "NUMERIC") == "\"x\" = 'NaN'"  # PostgreSQL numeric holds NaN
    assert pg_nan('"x"', "INTEGER") is None  # `int = 'NaN'` is a conversion error

    # NUMBER is Snowflake's DECIMAL and cannot hold NaN; only its FLOAT can.
    assert nan_predicate_builder("snowflake")('"x"', "DECIMAL(38, 0)") is None
    assert nan_predicate_builder("snowflake")('"x"', "FLOAT") == "\"x\" = 'NaN'"
    assert nan_predicate_builder("bigquery")('"x"', "FLOAT64") == 'IS_NAN("x")'
    assert nan_predicate_builder("trino")('"x"', "DOUBLE") == 'is_nan("x")'
    assert nan_predicate_builder("clickhouse")('"x"', "Float64") == 'isNaN("x")'

    # Engines that cannot store NaN at all: no predicate, so no cost and no risk.
    # SQLite coerces NaN to NULL on write; MySQL and SQL Server reject it outright.
    assert nan_predicate_builder("sqlite") is None
    assert nan_predicate_builder("mysql") is None
    assert nan_predicate_builder("mssql") is None


def test_nan_predicates_pass_guard_sql():
    """Every predicate is spliced into a WHERE that goes through guard_sql()."""
    from app.connectors.safety import guard_sql

    for kind in ("duckdb", "postgresql", "snowflake", "bigquery", "trino", "clickhouse"):
        predicate = nan_predicate_builder(kind)('"amount"', "DOUBLE")
        assert predicate is not None, kind
        guard_sql(f'SELECT COUNT(*) FROM "t" WHERE "amount" IS NOT NULL AND ({predicate})')


def test_string_length(ctx_factory):
    r = run_check_type(ctx_factory("email", {"min_len": 6}), "string_length")
    assert r.violation_count == 1  # "a@b"


_ZERO_INFLATED_Q = {"0.01": 0, "0.05": 0, "0.25": 0, "0.5": 0, "0.75": 5, "0.95": 20, "0.99": 50}


def test_quantile_bins_pool_ties_and_leave_the_lower_sentinel_empty():
    # zero-inflated baseline: p1..p50 all 0 -> several quantiles collapse onto one edge.
    # The merged bin must carry their pooled mass (it is P(0 <= x < p75) = 0.75), and the
    # [-inf, min) sentinel must carry NONE: nothing in the baseline sits below the
    # baseline's own minimum, and the 0.1 it used to get injected ~0.69 PSI on unchanged
    # data (#270).
    edges, expected = _quantile_bins({"quantiles": _ZERO_INFLATED_Q, "min": 0})
    assert abs(float(expected.sum()) - 1.0) < 1e-9
    assert edges[0] == -np.inf and float(expected[0]) == 0.0
    assert list(edges[1:]) == [0.0, 5.0, 20.0, 50.0, np.inf]
    assert float(expected[1]) == pytest.approx(0.75)  # p1..p50 ties + [p50, p75)


def test_quantile_bins_without_a_baseline_minimum_keep_the_lower_bin_real():
    # Older profiles may not carry `min`; then [-inf, p1) is a genuine 1% bin and must
    # keep its mass rather than being zeroed.
    edges, expected = _quantile_bins({"quantiles": _ZERO_INFLATED_Q})
    assert edges[0] == -np.inf
    assert float(expected[0]) == pytest.approx(0.01)


def test_numeric_drift_scores_zero_on_a_self_identical_zero_inflated_column():
    # The exact repro from #270: quantiles taken from the data itself must reproduce
    # the same histogram, so PSI is 0 — it used to be ~0.9 and failed the check.
    values = pd.Series([0.0] * 700 + [float(v) for v in np.linspace(0.5, 60, 300)])
    bcol = {
        "quantiles": {p: float(values.quantile(float(p)))
                      for p in ("0.01", "0.05", "0.25", "0.5", "0.75", "0.95", "0.99")},
        "min": float(values.min()),
    }
    r = _numeric_drift(values, bcol, 0.2)
    assert r.metrics["score"] < 0.01, r.metrics
    assert r.violation_count == 0


def test_numeric_drift_matching_data_scores_below_shifted():
    bcol = {"quantiles": _ZERO_INFLATED_Q, "min": 0}
    matching = pd.Series([0] * 70 + list(range(1, 31)))
    shifted = pd.Series(list(range(100, 200)))
    r_match = _numeric_drift(matching, bcol, 0.2)
    r_shift = _numeric_drift(shifted, bcol, 0.2)
    assert r_match.metrics["score"] < r_shift.metrics["score"]
    assert r_shift.violation_count == 1


def test_prefers_value_mix_separates_codes_from_amounts():
    # a 4-value payment code: the 10 stored values ARE the column -> value mix
    code = {"distinct_count": 4,
            "top_values": [{"value": v, "count": c} for v, c in ((1, 700), (2, 200), (3, 60), (4, 40))]}
    assert _prefers_value_mix(code, 1000) is True
    # a continuous amount: the stored values are 3% of the column -> quantile bins
    amount = {"distinct_count": 5000,
              "top_values": [{"value": float(i), "count": 3} for i in range(10)]}
    assert _prefers_value_mix(amount, 1000) is False
    # lumpy but very high cardinality: the tail carries structure 10 values can't hold
    lumpy_tail = {"distinct_count": 40_000,
                  "top_values": [{"value": 0, "count": 8000}]}
    assert _prefers_value_mix(lumpy_tail, 10_000) is False
    # mid coverage: the 10 stored values are only half the column, and everything else
    # folds into ONE __other__ bucket where a shift is invisible (PSI exactly 0.0). Half
    # a column is far too much to make blind, so this stays on the quantile path.
    half_covered = {"distinct_count": 300,
                    "top_values": [{"value": float(i), "count": 520} for i in range(10)]}
    assert _prefers_value_mix(half_covered, 9840) is False
    # no cardinality recorded (older profile) -> stay on the quantile path
    assert _prefers_value_mix({"top_values": [{"value": 1, "count": 900}]}, 1000) is False


def test_categorical_drift_matches_numeric_values_across_json_and_pandas():
    # The baseline stores JSON numbers (5) while pandas hands back float64 (5.0) for the
    # same column; comparing them as strings puts every row in __other__.
    bcol = {"top_values": [{"value": 5, "count": 60}, {"value": 7, "count": 40}],
            "null_count": 0}
    cur = pd.Series([5.0] * 60 + [7.0] * 40)
    r = _categorical_drift(cur, bcol, 100, 0.2, numeric=True)
    assert r.metrics["score"] < 1e-6, r.metrics
    assert r.metrics["kind"] == "numeric" and r.metrics["binning"] == "value"
    other = next(b for b in r.metrics["bins"] if b["category"] == "__other__")
    assert other["actual_pct"] == 0.0


def test_categorical_drift_denominator_is_full_table_nonnull():
    # 10 captured categories summing to 185; 35 rows live in the tail. __other__
    # expected mass must reflect that (~0.16), not collapse to 0 as it did when the
    # pandas sample size was used as the denominator.
    tops = [{"value": f"c{i}", "count": c} for i, c in enumerate([50, 40, 30, 20, 10, 9, 8, 7, 6, 5])]
    bcol = {"top_values": tops, "null_count": 0}
    cur = pd.Series([t["value"] for t in tops for _ in range(2)])
    r = _categorical_drift(cur, bcol, nonnull_total=220, threshold=0.2)
    other = next(b for b in r.metrics["bins"] if b["category"] == "__other__")
    assert other["expected_pct"] > 0.1


def test_regex_python_fallback(ctx_factory):
    r = run_check_type(
        ctx_factory("email", {"pattern": r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$"}), "regex_match"
    )
    assert r.violation_count == BAD_EMAILS
    assert r.metrics["engine"] == "python-fallback"


def test_freshness(ctx_factory):
    stale = run_check_type(ctx_factory("created_at", {"max_age_hours": 1}), "freshness")
    assert stale.violation_count == 1
    fresh = run_check_type(ctx_factory("created_at", {"max_age_hours": 100}), "freshness")
    assert fresh.violation_count == 0
    assert fresh.metrics["age_hours"] > 1
    # the future-dated row is excluded from staleness and surfaced as a metric
    assert fresh.metrics["future_rows"] == 1
    assert fresh.metrics["age_hours"] < 200  # not computed from the +10d row


def test_row_count_min(ctx_factory):
    low = run_check_type(ctx_factory(None, {"min_rows": SOURCE_ROWS + 1}), "row_count_min")
    assert low.violation_count == 1
    ok = run_check_type(ctx_factory(None, {"min_rows": 10}), "row_count_min")
    assert ok.violation_count == 0


def test_row_count_anomaly_needs_history(ctx_factory):
    r = run_check_type(ctx_factory(None, {}), "row_count_anomaly")
    assert r.violation_count == 0
    assert r.metrics["row_count"] == SOURCE_ROWS


class _MetadataConnector:
    def __init__(self, columns):
        self.columns = columns

    def get_columns(self, table, schema=None):
        assert table == "people"
        assert schema is None
        return self.columns


def test_schema_contract_passes_with_allowed_additive_column():
    connector = _MetadataConnector(
        [
            {"name": "id", "dtype": "INTEGER", "nullable": False},
            {"name": "email", "dtype": "TEXT", "nullable": True},
            {"name": "loaded_at", "dtype": "TIMESTAMP", "nullable": True},
        ]
    )
    ctx = CheckContext(
        connector=connector,
        table="people",
        schema=None,
        column=None,
        params={
            "expected_columns": [
                {"name": "id", "dtype": "INTEGER", "nullable": False},
                {"name": "email", "dtype": "TEXT", "nullable": True},
            ],
            "allow_additive": True,
        },
    )
    r = run_check_type(ctx, "schema_contract")
    assert r.violation_count == 0
    assert r.rows_evaluated is None
    assert [c["name"] for c in r.metrics["added"]] == ["loaded_at"]
    assert r.metrics["missing"] == []


def test_schema_contract_flags_missing_added_type_and_nullability_changes():
    connector = _MetadataConnector(
        [
            {"name": "id", "dtype": "BIGINT", "nullable": True},
            {"name": "status", "dtype": "TEXT", "nullable": True},
            {"name": "loaded_at", "dtype": "TIMESTAMP", "nullable": True},
        ]
    )
    ctx = CheckContext(
        connector=connector,
        table="people",
        schema=None,
        column=None,
        params={
            "expected_columns": [
                {"name": "id", "dtype": "INTEGER", "nullable": False},
                {"name": "email", "dtype": "TEXT", "nullable": True},
                {"name": "status", "dtype": "TEXT", "nullable": True},
            ],
            "allow_additive": False,
        },
    )
    r = run_check_type(ctx, "schema_contract")
    assert r.violation_count == 4
    assert [c["name"] for c in r.metrics["missing"]] == ["email"]
    assert [c["name"] for c in r.metrics["added"]] == ["loaded_at"]
    assert r.metrics["type_changed"] == [{"column": "id", "expected": "INTEGER", "actual": "BIGINT"}]
    assert r.metrics["nullability_changed"] == [{"column": "id", "expected": False, "actual": True}]


def test_custom_sql(ctx_factory):
    r = run_check_type(
        ctx_factory(None, {"sql": "SELECT * FROM people WHERE age > 500"}), "custom_sql"
    )
    assert r.violation_count == HUGE_AGE


def test_custom_sql_rejects_writes(ctx_factory):
    from app.connectors.safety import SqlNotAllowed

    with pytest.raises(SqlNotAllowed):
        run_check_type(ctx_factory(None, {"sql": "DELETE FROM people"}), "custom_sql")


def test_ml_outlier_finds_planted_row(ctx_factory):
    r = run_check_type(ctx_factory(None, {"contamination": 0.01}), "ml_outlier")
    assert r.rows_evaluated == SOURCE_ROWS
    flagged_ages = {row["age"] for row in r.sample_rows}
    assert 999 in flagged_ages  # the planted outlier must be caught
    assert r.scores and r.scores[0] >= max(r.scores)  # sorted by score desc


def test_validate_check():
    assert validate_check("not_null", "email", {}) == {}
    with pytest.raises(ValueError):
        validate_check("not_null", None, {})
    with pytest.raises(ValueError):
        validate_check("nope", "x", {})
    with pytest.raises(ValueError):
        validate_check("regex_match", "email", {"pattern": "("})  # invalid regex
    params = validate_check("accepted_values", "status", {"values": ["a"], "junk": 1})
    assert "junk" not in params
    contract = validate_check(
        "schema_contract",
        None,
        {"expected_columns": [{"name": "ID", "dtype": "INTEGER"}], "allow_additive": "false"},
    )
    assert contract["expected_columns"] == [{"name": "ID", "ordinal": 0, "dtype": "INTEGER"}]
    assert contract["allow_additive"] is False
    with pytest.raises(ValueError):
        validate_check("schema_contract", None, {"expected_columns": []})


def test_validate_drift_method():
    assert validate_check("distribution_drift", "x", {"method": "KS"})["method"] == "ks"
    assert validate_check("distribution_drift", "x", {})["method"] == "psi"  # default
    with pytest.raises(ValueError):
        validate_check("distribution_drift", "x", {"method": "wasserstein"})


def test_validate_schema_change():
    assert validate_check("schema_change", None, {"baseline": "pinned"})["baseline"] == "pinned"
    assert validate_check("schema_change", None, {})["baseline"] == "previous"  # default applied
    with pytest.raises(ValueError):
        validate_check("schema_change", None, {"baseline": "yesterday"})


def test_schema_change_baseline_captured(ctx_factory):
    # No db context -> first-run baseline capture, no violation.
    r = run_check_type(ctx_factory(None, {"baseline": "previous"}), "schema_change")
    assert r.violation_count == 0
    assert r.metrics["note"] == "baseline captured"
    assert r.metrics["column_count"] == 6  # people: id, email, age, status, score, created_at


def test_diff_schemas_unit():
    from app.core.schema_monitor import diff_schemas

    base = [
        {"name": "a", "dtype": "INTEGER", "nullable": True, "ordinal": 0},
        {"name": "b", "dtype": "TEXT", "nullable": True, "ordinal": 1},
    ]
    cur = [
        {"name": "a", "dtype": "BIGINT", "nullable": False, "ordinal": 0},
        {"name": "c", "dtype": "TEXT", "nullable": True, "ordinal": 1},
    ]
    d = diff_schemas(base, cur)
    assert [x["name"] for x in d["removed"]] == ["b"]
    assert [x["name"] for x in d["added"]] == ["c"]
    assert d["type_changed"] == [{"column": "a", "from": "INTEGER", "to": "BIGINT"}]
    assert d["nullability_changed"] == [{"column": "a", "from": True, "to": False}]
    assert d["reordered"] is False


# --------------------------------------------------------------- distribution_drift
# These tests need a baseline Profile (PSI) and run history (KS), so they build a
# real source table, profile it into the app DB, and run via a db-aware context.

from app.core.profiler import profile_dataset  # noqa: E402
from app.db import init_db, session_factory  # noqa: E402
from app.models import Check, CheckRun, Connection, Dataset, Profile  # noqa: E402


def _make_source(tmp_dir: Path, name: str, columns: dict[str, list]) -> str:
    """Write a one-table sqlite DB ('t') with the given columns; return its DSN."""
    path = tmp_dir / f"{name}.sqlite"
    con = sqlite3.connect(path)
    coldefs = ", ".join(
        f"{c} {'REAL' if isinstance(v[0], float) else 'TEXT' if isinstance(v[0], str) else 'INTEGER'}"
        for c, v in columns.items()
    )
    con.execute(f"CREATE TABLE t ({coldefs})")
    names = list(columns)
    n = len(next(iter(columns.values())))
    placeholders = ", ".join(["?"] * len(names))
    con.executemany(
        f"INSERT INTO t ({', '.join(names)}) VALUES ({placeholders})",
        [tuple(columns[c][i] for c in names) for i in range(n)],
    )
    con.commit()
    con.close()
    return f"sqlite:///{path.as_posix()}"


def _profiled_ctx(
    db,
    dsn: str,
    check_type: str,
    column: str | None,
    params: dict,
    baseline_dsn: str | None = None,
):
    """Persist a Connection/Dataset, profile `baseline_dsn` (default = dsn) into a
    Profile row, create a Check, and return a db-aware CheckContext over `dsn`."""
    # Monotonic per-process counter — the session-shared app DB requires a globally
    # unique connection name, and id(params) is unreliable (CPython reuses the id of a
    # short-lived dict after GC, so consecutive drift tests collided on CI).
    _profiled_ctx.seq = getattr(_profiled_ctx, "seq", 0) + 1
    conn = Connection(
        name=f"c-{check_type}-{column}-{_profiled_ctx.seq}", kind="sqlite", dsn=dsn
    )
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, schema_name=None, table_name="t")
    db.add(ds)
    db.flush()

    prof = profile_dataset(Connector(baseline_dsn or dsn), "t", None, sample_rows=10_000)
    db.add(
        Profile(
            dataset_id=ds.id,
            row_count=prof["row_count"],
            sampled_rows=prof["sampled_rows"],
            columns=prof["columns"],
            table_facts=prof["table_facts"],
        )
    )
    chk = Check(
        dataset_id=ds.id, name=check_type, check_type=check_type,
        column_name=column, params=validate_check(check_type, column, params),
        severity="warn", status="active",
    )
    db.add(chk)
    db.commit()
    return CheckContext(
        connector=Connector(dsn), table="t", schema=None, column=column,
        params=chk.params, db=db, check_id=chk.id,
    )


def _drift_ctx(db, dsn: str, column: str, params: dict, baseline_dsn: str | None = None):
    return _profiled_ctx(db, dsn, "distribution_drift", column, params, baseline_dsn)


@pytest.fixture(scope="module")
def drift_tmp() -> Path:
    # pytest's tmp_path basetemp is unwritable on this machine (OneDrive/Temp ACLs);
    # mirror conftest's own mkdtemp approach instead.
    return Path(tempfile.mkdtemp(prefix="dqsentinel-drift-"))


@pytest.fixture
def app_db():
    init_db()
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _monitor_ctx(db, dsn: str, check_type: str, column: str | None, params: dict):
    _monitor_ctx.seq = getattr(_monitor_ctx, "seq", 0) + 1
    conn = Connection(name=f"m-{check_type}-{_monitor_ctx.seq}", kind="sqlite", dsn=dsn)
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, schema_name=None, table_name="people")
    db.add(ds)
    db.flush()
    chk = Check(
        dataset_id=ds.id,
        name=f"{check_type}-{_monitor_ctx.seq}",
        check_type=check_type,
        column_name=column,
        params=params,
        severity="warn",
        status="active",
    )
    db.add(chk)
    db.commit()
    return (
        CheckContext(
            connector=Connector(dsn),
            table="people",
            schema=None,
            column=column,
            params=params,
            db=db,
            check_id=chk.id,
        ),
        chk,
    )


def _add_run(db, check: Check, metrics: dict, status: str = "pass") -> None:
    db.add(
        CheckRun(
            check_id=check.id,
            dataset_id=check.dataset_id,
            started_at=_now_naive(),
            status=status,
            violation_count=0,
            metrics=metrics,
        )
    )


def test_freshness_adaptive_uses_default_with_insufficient_history(app_db, source_db):
    params = {
        "strategy": "adaptive",
        "default_max_age_hours": 100,
        "min_history": 3,
        "lookback_runs": 5,
    }
    ctx, _check = _monitor_ctx(app_db, source_db, "freshness", "created_at", params)
    r = run_check_type(ctx, "freshness")
    assert r.violation_count == 0
    assert r.metrics["threshold_source"] == "default"
    assert r.metrics["history_n"] == 0
    assert r.metrics["max_age_hours"] == 100
    assert r.metrics["note"] == "insufficient freshness history; using configured default"


def test_freshness_adaptive_uses_history_threshold(app_db, drift_tmp):
    now = _now_naive()
    dsn = _make_source(
        drift_tmp,
        "fresh_adaptive",
        {"ts": [(now - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")]},
    )
    params = {
        "strategy": "adaptive",
        "default_max_age_hours": 24,
        "min_history": 3,
        "lookback_runs": 5,
        "multiplier": 0.5,
        "grace_hours": 0,
    }
    ctx, check = _monitor_ctx(app_db, dsn, "freshness", "ts", params)
    ctx.table = "t"
    for hours_ago in (18, 12, 6):
        _add_run(
            app_db,
            check,
            {"latest": (now - timedelta(hours=hours_ago)).isoformat(), "age_hours": hours_ago},
        )
    app_db.commit()

    r = run_check_type(ctx, "freshness")
    assert r.violation_count == 1
    assert r.metrics["threshold_source"] == "history"
    assert r.metrics["history_n"] == 3
    assert r.metrics["intervals_n"] == 2
    assert r.metrics["observed_interval_hours"] == 6.0
    assert r.metrics["max_age_hours"] == 3.0


def test_row_count_adaptive_builds_baseline(app_db, source_db):
    params = {"strategy": "adaptive", "min_history": 3}
    ctx, _check = _monitor_ctx(app_db, source_db, "row_count_anomaly", None, params)
    r = run_check_type(ctx, "row_count_anomaly")
    assert r.violation_count == 0
    assert r.metrics["history_n"] == 0
    assert r.metrics["note"] == "collecting adaptive baseline"
    assert r.metrics["row_count"] == SOURCE_ROWS


def test_row_count_adaptive_flags_out_of_bounds(app_db, source_db):
    params = {"strategy": "adaptive", "min_history": 5, "lookback_runs": 10, "multiplier": 3.5}
    ctx, check = _monitor_ctx(app_db, source_db, "row_count_anomaly", None, params)
    for row_count in (100, 101, 100, 99, 100):
        _add_run(app_db, check, {"row_count": row_count})
    app_db.commit()

    r = run_check_type(ctx, "row_count_anomaly")
    assert r.violation_count == 1
    assert r.metrics["history_n"] == 5
    assert r.metrics["baseline_center"] == 100.0
    assert r.metrics["lower_bound"] < 100 < r.metrics["upper_bound"]
    assert r.metrics["row_count"] == SOURCE_ROWS


def test_drift_numeric_stable_passes(app_db, drift_tmp):
    rng = np.random.default_rng(1)
    base = list(rng.normal(0, 1, 4000))
    cur = list(np.random.default_rng(2).normal(0, 1, 4000))
    base_dsn = _make_source(drift_tmp, "num_base", {"v": base})
    cur_dsn = _make_source(drift_tmp, "num_cur_stable", {"v": cur})
    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.metrics["method"] == "psi"
    assert r.metrics["score"] < 0.1, r.metrics
    assert r.violation_count == 0
    assert r.metrics["bins"] and "baseline_profile_id" in r.metrics


def test_drift_numeric_shift_fails(app_db, drift_tmp):
    base = list(np.random.default_rng(1).normal(0, 1, 4000))
    cur = list(np.random.default_rng(3).normal(3, 1, 4000))  # mean shifted +3σ
    base_dsn = _make_source(drift_tmp, "num_base2", {"v": base})
    cur_dsn = _make_source(drift_tmp, "num_cur_shift", {"v": cur})
    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.metrics["score"] > 0.2, r.metrics
    assert r.violation_count == 1
    assert len(r.metrics["bins"]) >= 5  # decile bins present in metrics
    assert "PSI" in r.detail and "baseline profile" in r.detail


def test_drift_categorical_stable_passes(app_db, drift_tmp):
    base = (["a"] * 600) + (["b"] * 300) + (["c"] * 100)
    cur = (["a"] * 590) + (["b"] * 305) + (["c"] * 105)
    base_dsn = _make_source(drift_tmp, "cat_base", {"v": base})
    cur_dsn = _make_source(drift_tmp, "cat_cur_stable", {"v": cur})
    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.metrics["kind"] == "categorical"
    assert r.violation_count == 0, r.metrics
    assert any(b["category"] == "__other__" for b in r.metrics["bins"])


def test_drift_categorical_vanished_category_fails(app_db, drift_tmp):
    base = (["a"] * 400) + (["b"] * 400) + (["c"] * 200)
    cur = (["a"] * 990) + (["d"] * 10)  # b & c vanished, new 'd' appears
    base_dsn = _make_source(drift_tmp, "cat_base2", {"v": base})
    cur_dsn = _make_source(drift_tmp, "cat_cur_vanish", {"v": cur})
    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.violation_count == 1, r.metrics
    assert r.metrics["score"] > 0.2


def test_drift_no_profile_passes_with_message(app_db, drift_tmp):
    # build a context whose dataset has NO profile row
    dsn = _make_source(drift_tmp, "noprof", {"v": [1.0, 2.0, 3.0, 4.0]})
    conn = Connection(name="c-noprof", kind="sqlite", dsn=dsn)
    app_db.add(conn)
    app_db.flush()
    ds = Dataset(connection_id=conn.id, schema_name=None, table_name="t")
    app_db.add(ds)
    app_db.flush()
    chk = Check(
        dataset_id=ds.id, name="d", check_type="distribution_drift", column_name="v",
        params={"method": "psi"}, severity="warn", status="active",
    )
    app_db.add(chk)
    app_db.commit()
    ctx = CheckContext(
        connector=Connector(dsn), table="t", schema=None, column="v",
        params={"method": "psi"}, db=app_db, check_id=chk.id,
    )
    r = run_check_type(ctx, "distribution_drift")
    assert r.violation_count == 0
    assert "no baseline profile" in r.detail


# --------------------------------------------------------------- #263 ml_outlier features


def _ml_source(tmp_dir: Path, name: str) -> tuple[str, list[int]]:
    """A table shaped like a real fact table: surrogate key, timestamp, zone code and
    two actual measurements, with three planted multivariate outliers."""
    rng = np.random.default_rng(5)
    n = 400
    amount = [float(x) for x in rng.normal(100, 8, n)]
    quantity = [float(x) for x in rng.normal(3, 0.5, n)]
    planted = [50, 200, 350]
    for i in planted:
        amount[i], quantity[i] = 5000.0, 80.0
    start = datetime(2024, 1, 1)
    cols = {
        "order_id": list(range(1, n + 1)),
        "created_at": [(start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
                       for i in range(n)],
        "zone_id": [int(x) for x in rng.integers(1, 266, n)],
        "amount": amount,
        "quantity": quantity,
    }
    return _make_source(tmp_dir, name, cols), planted


def test_ml_outlier_excludes_identifier_and_non_numeric_columns(app_db, drift_tmp):
    # `SELECT *` hands the whole row to IsolationForest, so the surrogate key, the
    # timestamp and the zone code became "features" and the newest/oldest rows scored as
    # anomalies (#263). The profile knows enough to drop all three.
    dsn, planted = _ml_source(drift_tmp, "ml_features")
    ctx = _profiled_ctx(app_db, dsn, "ml_outlier", None, {"contamination": 0.01})
    r = run_check_type(ctx, "ml_outlier")

    assert r.metrics["feature_source"] == "profile"
    assert set(r.metrics["features"]) == {"amount", "quantity"}
    excluded = {e["column"] for e in r.metrics["excluded_features"]}
    assert {"order_id", "created_at", "zone_id"} <= excluded
    # the planted rows are the outliers; the id extremes are not
    flagged = {row["order_id"] for row in r.sample_rows}
    assert {i + 1 for i in planted} <= flagged
    assert 1 not in flagged and 400 not in flagged


def test_ml_outlier_columns_param_overrides_the_profile(app_db, drift_tmp):
    dsn, _planted = _ml_source(drift_tmp, "ml_explicit")
    ctx = _profiled_ctx(
        app_db, dsn, "ml_outlier", None,
        {"contamination": 0.01, "columns": ["order_id", "amount"]},
    )
    r = run_check_type(ctx, "ml_outlier")
    assert r.metrics["feature_source"] == "params"
    assert set(r.metrics["features"]) == {"order_id", "amount"}


def test_ml_outlier_skips_when_too_few_real_features_remain(app_db, drift_tmp):
    # Nothing but a key, a timestamp and one measurement: a "multivariate" outlier check
    # has no second dimension, so it reports why instead of scoring the id column.
    start = datetime(2024, 1, 1)
    dsn = _make_source(
        drift_tmp, "ml_thin",
        {
            "order_id": list(range(1, 201)),
            "created_at": [(start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
                           for i in range(200)],
            "amount": [float(x) for x in np.random.default_rng(3).normal(10, 2, 200)],
        },
    )
    ctx = _profiled_ctx(app_db, dsn, "ml_outlier", None, {"contamination": 0.01})
    r = run_check_type(ctx, "ml_outlier")
    assert r.violation_count == 0
    assert r.metrics["note"] == "fewer than 2 usable numeric features"
    assert "columns" in r.detail


def test_ml_outlier_keeps_all_distinct_float_measurements(app_db, drift_tmp):
    # `pk_candidates` only means "no nulls and distinct == row_count" — which a
    # continuous measurement (a sensor reading, a price, a latency) satisfies routinely.
    # Excluding every pk_candidate with no integrality test dropped those measurements as
    # "primary-key candidate", left fewer than 2 features, and made ml_outlier pass with a
    # note — i.e. silently stop working on exactly the columns it exists for. Only an
    # INTEGER near-unique column may be dropped as a surrogate key.
    rng = np.random.default_rng(17)
    n = 300
    temperature = [float(x) for x in rng.normal(20.0, 1.5, n)]
    pressure = [float(x) for x in rng.normal(1000.0, 12.0, n)]
    planted = [40, 150, 260]
    for k, i in enumerate(planted):  # distinct values, so the columns stay all-distinct
        temperature[i], pressure[i] = 95.0 + k, 300.0 + k
    dsn = _make_source(
        drift_tmp, "ml_measurements",
        # `serial` is an integer surrogate key with a name no token rule catches, so the
        # only thing that can drop it is the cardinality/dtype rule under test.
        {"serial": list(range(1, n + 1)), "temperature": temperature, "pressure": pressure},
    )
    ctx = _profiled_ctx(app_db, dsn, "ml_outlier", None, {"contamination": 0.01})
    r = run_check_type(ctx, "ml_outlier")

    assert set(r.metrics["features"]) == {"temperature", "pressure"}, r.metrics
    assert {e["column"] for e in r.metrics["excluded_features"]} == {"serial"}
    assert "note" not in r.metrics  # the check actually ran
    assert {i + 1 for i in planted} <= {row["serial"] for row in r.sample_rows}


# ------------------------------------------------- #265 / #270: PSI on unchanged data
# Both issues are the same failure seen through different column shapes: a
# distribution_drift check firing on its OWN baseline. These are auto-active
# monitor-pack checks, so a false fire here is a day-one incident on data that never
# changed. The matrix pins both halves — identical data must pass, genuinely shifted
# data must still fail — across every shape the two issues name.


def _drift_shapes() -> dict[str, tuple[list, list, str]]:
    """name -> (baseline values, genuinely shifted values, expected binning strategy)."""
    rng = np.random.default_rng(11)
    zones = np.arange(1, 266)
    zone_w = 1.0 / np.power(rng.permutation(zones), 0.9)
    zone_w = zone_w / zone_w.sum()
    return {
        # single-distinct: the phantom [-inf, min) bin alone scored PSI 0.7006 (#270)
        "constant": ([5.0] * 2000, [9.0] * 2000, "value"),
        # zero-inflated / left-bounded: p1..p50 collapse onto the minimum (#270)
        "zero_inflated": (
            [0.0] * 3000 + [float(x) for x in rng.exponential(8.0, 2000)],
            [0.0] * 1000 + [float(x) for x in rng.exponential(8.0, 4000)],
            "quantile",
        ),
        "left_bounded": (
            [float(x) for x in rng.lognormal(1.0, 0.8, 3000)],
            [float(x) for x in rng.lognormal(2.0, 0.8, 3000)],
            "quantile",
        ),
        # discrete integer zone id, NYC taxi DOLocationID-like: scored 0.3631 (#265)
        "discrete_integer": (
            [int(x) for x in rng.choice(zones, 6000, p=zone_w)],
            [int(x) for x in rng.integers(200, 266, 6000)],
            "quantile",
        ),
        # low-cardinality numeric code: quantile bins scored > 2 against itself (#265)
        "numeric_code": (
            [int(x) for x in rng.choice([1, 2, 3, 4, 5], 4000, p=[0.65, 0.28, 0.04, 0.02, 0.01])],
            [int(x) for x in rng.choice([1, 2, 3, 4, 5], 4000, p=[0.2, 0.2, 0.3, 0.2, 0.1])],
            "value",
        ),
        "binary_flag": (
            [int(x) for x in rng.choice([0, 1], 4000, p=[0.3, 0.7])],
            [int(x) for x in rng.choice([0, 1], 4000, p=[0.7, 0.3])],
            "value",
        ),
        # the shape that already worked — it must not regress
        "continuous": (
            [float(x) for x in rng.normal(0, 1, 3000)],
            [float(x) for x in rng.normal(3, 1, 3000)],
            "quantile",
        ),
    }


DRIFT_SHAPES = _drift_shapes()


@pytest.mark.parametrize("shape", sorted(DRIFT_SHAPES))
def test_drift_psi_is_zero_against_an_identical_baseline(app_db, drift_tmp, shape):
    base, _shifted, binning = DRIFT_SHAPES[shape]
    dsn = _make_source(drift_tmp, f"same_{shape}", {"v": base})
    ctx = _drift_ctx(app_db, dsn, "v", {"method": "psi"})  # baseline == current table
    r = run_check_type(ctx, "distribution_drift")
    assert r.violation_count == 0, r.metrics
    assert r.metrics["score"] < 0.1, r.metrics
    assert r.metrics["binning"] == binning, r.metrics


@pytest.mark.parametrize("shape", sorted(DRIFT_SHAPES))
def test_drift_psi_still_fails_on_a_real_shift(app_db, drift_tmp, shape):
    base, shifted, _binning = DRIFT_SHAPES[shape]
    base_dsn = _make_source(drift_tmp, f"shift_base_{shape}", {"v": base})
    cur_dsn = _make_source(drift_tmp, f"shift_cur_{shape}", {"v": shifted})
    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.metrics["score"] >= 0.2, r.metrics
    assert r.violation_count == 1, r.metrics


@pytest.mark.parametrize(
    ("label", "values"),
    [("int_codes", [1] * 1000 + [2] * 2000), ("str_codes", ["a"] * 1000 + ["b"] * 2000)],
)
def test_drift_value_mix_recounts_in_sql_when_the_sampled_read_is_truncated(
    app_db, drift_tmp, label, values
):
    # 3000 rows: the first 1000 carry one code, the rest another. The check's own read
    # is capped at 1000 rows, so the sampled frame sees ONLY the first code while the
    # baseline top_values are full-table counts — comparing those two populations
    # reports drift on a table nobody touched (this is why NYC taxi `payment_type`
    # scored 0.31 comparing full-table shares against the first 50k rows). The current
    # side must be counted over the same population the baseline came from.
    dsn = _make_source(drift_tmp, f"trunc_{label}", {"v": values})
    ctx = _drift_ctx(app_db, dsn, "v", {"method": "psi", "max_rows": 1000})
    r = run_check_type(ctx, "distribution_drift")
    assert r.metrics["score"] < 0.01, r.metrics
    assert r.violation_count == 0
    assert r.metrics["binning"] == "value"
    assert r.rows_evaluated == 3000  # whole table, not the 1000-row sample


def _mid_coverage_column(tail_start: int) -> list[float]:
    """10 head values carrying ~53% of the rows, plus a 290-value tail carrying the rest.

    The tail starts at ``tail_start`` so two of these frames can share a head and have
    disjoint tails — a shift that lives entirely outside the profile's stored top 10.
    """
    head = [float(v) for v in range(10) for _ in range(520)]  # 5200 rows
    tail = [float(tail_start + v) for v in range(290) for _ in range(16)]  # 4640 rows
    return head + tail


def test_drift_tail_confined_shift_on_a_mid_coverage_column_is_still_detected(
    app_db, drift_tmp
):
    # The value path keeps the baseline's 10 top values and folds EVERYTHING else into a
    # single __other__ bucket. A shift whose source AND destination both live in that
    # tail therefore leaves all 11 bins bit-identical and scores PSI exactly 0.0 at any
    # magnitude — it is not seen at all. A column whose top 10 cover only ~half the rows
    # must not be routed there: here the entire 47% tail is replaced by never-seen
    # values, which the quantile path (which resolves the tail) catches.
    base = _mid_coverage_column(1000)
    shifted = _mid_coverage_column(50_000)
    base_dsn = _make_source(drift_tmp, "tail_shift_base", {"v": base})
    cur_dsn = _make_source(drift_tmp, "tail_shift_cur", {"v": shifted})

    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.metrics["binning"] == "quantile", r.metrics  # not the __other__ blind spot
    assert r.metrics["score"] >= 0.2, r.metrics
    assert r.violation_count == 1, r.metrics

    # ...and the same column against its OWN baseline must still not fire: the false
    # fires of #265/#270 are what put the value path there in the first place.
    same = run_check_type(
        _drift_ctx(app_db, base_dsn, "v", {"method": "psi"}), "distribution_drift"
    )
    assert same.violation_count == 0, same.metrics
    assert same.metrics["score"] < 0.1, same.metrics


def test_drift_numeric_new_values_below_the_baseline_minimum_are_caught(app_db, drift_tmp):
    # The [-inf, min) sentinel carries no baseline mass — which must mean "nothing was
    # ever seen down here", not "we stopped looking". Negative values arriving in a
    # previously non-negative column are exactly the drift the bin exists for.
    base = [float(x) for x in np.random.default_rng(4).lognormal(1.0, 0.6, 3000)]
    cur = base[:1500] + [-5.0] * 1500
    base_dsn = _make_source(drift_tmp, "sentinel_base", {"v": base})
    cur_dsn = _make_source(drift_tmp, "sentinel_cur", {"v": cur})
    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.metrics["bins"][0]["expected_pct"] == 0.0
    assert r.metrics["bins"][0]["actual_pct"] == pytest.approx(0.5, abs=0.01)
    assert r.violation_count == 1, r.metrics


def test_drift_ks_first_run_captures_then_shift_fails(app_db, drift_tmp):
    base = list(np.random.default_rng(1).normal(0, 1, 3000))
    base_dsn = _make_source(drift_tmp, "ks_base", {"v": base})
    ctx = _drift_ctx(app_db, base_dsn, "v", {"method": "ks", "threshold": 0.05})

    # First run: no prior sample -> baseline captured, passes.
    r1 = run_check_type(ctx, "distribution_drift")
    assert r1.violation_count == 0
    assert r1.metrics["note"] == "baseline captured"
    assert 0 < len(r1.metrics["drift_sample"]) <= 2000

    # Persist that run (the runner normally does this) so the next run sees a prior.
    dataset_id = app_db.get(Check, ctx.check_id).dataset_id
    app_db.add(
        CheckRun(
            check_id=ctx.check_id, dataset_id=dataset_id,
            status="pass", violation_count=0, metrics=dict(r1.metrics),
        )
    )
    app_db.commit()

    # Second run: point the SAME check at a shifted table -> KS should fail.
    shifted_dsn = _make_source(
        drift_tmp, "ks_shift", {"v": list(np.random.default_rng(9).normal(4, 1, 3000))}
    )
    ctx2 = CheckContext(
        connector=Connector(shifted_dsn), table="t", schema=None, column="v",
        params={"method": "ks", "threshold": 0.05}, db=app_db, check_id=ctx.check_id,
    )
    r2 = run_check_type(ctx2, "distribution_drift")
    assert r2.metrics["prior_n"] >= 2
    assert r2.metrics["score"] <= 0.05  # p-value tiny
    assert r2.violation_count == 1
