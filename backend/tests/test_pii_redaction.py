import pytest

from app.llm.client import format_rows
from app.llm.privacy import guard_agent_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT email AS e FROM people",
        "SELECT LOWER(email) FROM people",
        "SELECT email || 'x' FROM people",
        "SELECT SUBSTR(email, 1, 3) FROM people",
        "SELECT MAX(email) FROM people",
        "SELECT * FROM people",
        "SELECT p.* FROM people p",
        "SELECT p FROM people p",
        "SELECT row_to_json(p) FROM people p",
        "WITH x(e) AS (SELECT email FROM people) SELECT e FROM x",
        "SELECT e FROM (SELECT email AS e FROM people) x",
        "SELECT id FROM people UNION ALL SELECT email FROM people",
        'SELECT "EMAIL" AS safe FROM people',
    ],
)
def test_agent_sql_blocks_pii_projections(sql):
    with pytest.raises(ValueError, match="PII"):
        guard_agent_sql(sql, ["email"], "sqlite")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) AS n FROM people",
        "SELECT COUNT(email) AS n FROM people",
        "SELECT COUNT(DISTINCT email) AS n FROM people",
        "SELECT id, status FROM people WHERE email IS NULL",
        "SELECT status, COUNT(email) AS n FROM people GROUP BY status",
        "WITH x AS (SELECT COUNT(email) AS n FROM people) SELECT n FROM x",
    ],
)
def test_agent_sql_allows_non_pii_and_counts(sql):
    assert guard_agent_sql(sql, ["email"], "sqlite") == sql


def test_unparseable_sql_fails_closed():
    with pytest.raises(ValueError, match="verify PII safety"):
        guard_agent_sql("SELECT (", ["email"], "sqlite")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COLUMNS('email') FROM people",
        "SELECT #2 FROM people",
        "SELECT e FROM query('SELECT email AS e FROM people')",
        "SELECT e FROM people AS p(id, e, age)",
    ],
)
def test_dynamic_projections_fail_closed(sql):
    with pytest.raises(ValueError, match="PII"):
        guard_agent_sql(sql, ["email"], "duckdb")


def test_no_pii_still_uses_source_guard():
    with pytest.raises(ValueError):
        guard_agent_sql("SELECT * INTO backup FROM people", [], "sqlite")


def test_llm_table_samples_never_exceed_25_rows():
    rendered = format_rows(["value"], [[f"row-{i}"] for i in range(30)])
    assert "row-24" in rendered
    assert "row-25" not in rendered
    assert "30 rows; showing first 25" in rendered
