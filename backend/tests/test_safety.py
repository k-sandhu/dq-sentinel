import pytest

from app.connectors.safety import SqlNotAllowed, enforce_limit, guard_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t",
        "select count(*) from orders where status = 'paid'",
        "WITH x AS (SELECT 1 AS a) SELECT * FROM x",
        "SELECT * FROM t -- trailing comment",
        "SELECT * FROM t;",  # single trailing semicolon is tolerated
        "SELECT * FROM t WHERE note = 'please create table'",  # keyword inside a literal
        "SELECT * FROM t WHERE note = 'a; b'",  # semicolon inside a literal
    ],
)
def test_allows_readonly(sql):
    assert guard_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "CREATE TABLE x (a int)",
        "SELECT 1; DROP TABLE t",
        "PRAGMA table_info(t)",
        "ATTACH DATABASE 'x' AS y",
        "SELECT * FROM t; SELECT * FROM u",
        "/* sneaky */ DELETE FROM t",
        "WITH x AS (SELECT 1) UPDATE t SET a = 1",
        "EXPLAIN SELECT 1",  # not a plain SELECT/WITH
        "",
    ],
)
def test_rejects_writes_and_tricks(sql):
    with pytest.raises(SqlNotAllowed):
        guard_sql(sql)


def test_enforce_limit_wraps():
    wrapped = enforce_limit("SELECT * FROM t", 10)
    assert wrapped.endswith("LIMIT 10")
    assert "SELECT * FROM (" in wrapped
