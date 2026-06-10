import pytest

from app.connectors.sa import Connector
from app.core.check_types import CheckContext, run_check_type, validate_check
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


def test_range(ctx_factory):
    r = run_check_type(ctx_factory("age", {"min": 0, "max": 120}), "range")
    assert r.violation_count == HUGE_AGE
    assert r.sample_rows[0]["age"] == 999


def test_string_length(ctx_factory):
    r = run_check_type(ctx_factory("email", {"min_len": 6}), "string_length")
    assert r.violation_count == 1  # "a@b"


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


def test_row_count_min(ctx_factory):
    low = run_check_type(ctx_factory(None, {"min_rows": SOURCE_ROWS + 1}), "row_count_min")
    assert low.violation_count == 1
    ok = run_check_type(ctx_factory(None, {"min_rows": 10}), "row_count_min")
    assert ok.violation_count == 0


def test_row_count_anomaly_needs_history(ctx_factory):
    r = run_check_type(ctx_factory(None, {}), "row_count_anomaly")
    assert r.violation_count == 0
    assert r.metrics["row_count"] == SOURCE_ROWS


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
