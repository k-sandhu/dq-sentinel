import time

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
        'SELECT "delete" FROM t',
        "SELECT `update` FROM t",
        "SELECT [insert] FROM t",
        'SELECT "a;drop" FROM t',
        "SELECT * FROM t -- delete; from comment",
        "SELECT /* update; comment */ * FROM t",
        "SELECT $$delete; update$$ AS note",
        "SELECT $tag$drop; alter$tag$ AS note",
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
        'SELECT "delete" FROM t; DROP TABLE t',
        "SELECT `update` FROM t; UPDATE t SET a = 1",
        "SELECT [insert] FROM t; SELECT * FROM u",
        "SELECT 1;;",
        "SELECT * FROM t /* unterminated ; DROP TABLE t",
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


# --- read-side file / network / OS functions (#281; DuckDB host-file read is #267) ---


@pytest.mark.parametrize(
    ("sql", "fn"),
    [
        # DuckDB: the verified #267 repro plus the rest of the file/HTTP scanners.
        ("SELECT read_text('/etc/hostname') AS leak", "READ_TEXT"),
        ("SELECT * FROM read_csv('/etc/passwd')", "READ_CSV"),
        ("SELECT * FROM read_csv_auto('/etc/passwd')", "READ_CSV_AUTO"),
        ("SELECT * FROM read_blob('/etc/shadow')", "READ_BLOB"),
        ("SELECT * FROM read_parquet('s3://bucket/secrets.parquet')", "READ_PARQUET"),
        ("SELECT * FROM read_json_auto('http://169.254.169.254/latest/meta-data')", "READ_JSON_AUTO"),
        ("SELECT * FROM parquet_scan('/data/secrets.parquet')", "PARQUET_SCAN"),
        ("SELECT * FROM postgres_query('db', 'select 1')", "POSTGRES_QUERY"),
        ("SELECT * FROM sqlite_scan('/data/app.sqlite', 'users')", "SQLITE_SCAN"),
        # MySQL.
        ("SELECT load_file('/etc/passwd') AS leak", "LOAD_FILE"),
        # ClickHouse table functions.
        ("SELECT * FROM url('http://evil.example/x.csv', CSV)", "URL"),
        ("SELECT * FROM s3('https://b.s3.amazonaws.com/k', 'CSV')", "S3"),
        ("SELECT * FROM file('/etc/passwd', 'LineAsString')", "FILE"),
        ("SELECT * FROM mysql('host:3306', 'db', 'tbl', 'user', 'pw')", "MYSQL"),
        ("SELECT * FROM postgresql('host:5432', 'db', 'tbl', 'user', 'pw')", "POSTGRESQL"),
        ("SELECT * FROM remote('other-host', 'db.tbl')", "REMOTE"),
        ("SELECT * FROM executable('leak.sh', 'TabSeparated')", "EXECUTABLE"),
        # PostgreSQL family.
        ("SELECT pg_read_file('/etc/passwd')", "PG_READ_FILE"),
        ("SELECT pg_read_binary_file('/etc/shadow')", "PG_READ_BINARY_FILE"),
        ("SELECT pg_ls_dir('/')", "PG_LS_DIR"),
        ("SELECT lo_import('/etc/passwd')", "LO_IMPORT"),
        ("SELECT * FROM dblink('dbname=other', 'select 1') AS x(a int)", "DBLINK"),
        ("SELECT query_to_xml('select 1', true, true, '')", "QUERY_TO_XML"),
        # SQLite extension functions.
        ("SELECT readfile('/etc/passwd')", "READFILE"),
        ("SELECT writefile('/tmp/pwn', 'x')", "WRITEFILE"),
        ("SELECT load_extension('/tmp/evil.so')", "LOAD_EXTENSION"),
        # SQL Server.
        ("SELECT * FROM openrowset(BULK '/etc/passwd', SINGLE_CLOB) AS x", "OPENROWSET"),
        ("SELECT * FROM openquery(linked, 'select 1')", "OPENQUERY"),
        ("SELECT xp_cmdshell('whoami')", "XP_CMDSHELL"),
        # Snowflake / BigQuery.
        ("SELECT * FROM external_query('conn', 'select 1')", "EXTERNAL_QUERY"),
    ],
)
def test_rejects_file_and_network_functions(sql, fn):
    with pytest.raises(SqlNotAllowed) as excinfo:
        guard_sql(sql)
    assert f"{fn}()" in str(excinfo.value)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT READ_TEXT('/etc/hostname')",  # upper case
        "select ReAd_TeXt('/etc/hostname')",  # mixed case
        "SELECT read_text ('/etc/hostname')",  # space before paren
        "SELECT read_text\n\t('/etc/hostname')",  # newline/tab before paren
        "SELECT read_text /* sneaky */ ('/etc/hostname')",  # comment before paren
        "SELECT pg_catalog.pg_read_file('/etc/passwd')",  # schema-qualified
        "SELECT PG_CATALOG.PG_LS_DIR('/')",  # schema-qualified, upper case
        'SELECT "pg_read_file"(\'/etc/passwd\')',  # quoted identifier as function name
        "SELECT `load_file`('/etc/passwd')",  # backtick-quoted (MySQL)
        "SELECT [readfile]('/etc/passwd')",  # bracket-quoted (SQL Server)
        "WITH x AS (SELECT read_text('/etc/hostname') AS c) SELECT * FROM x",  # inside a CTE
        "SELECT * FROM t WHERE a IN (SELECT read_text('/etc/hostname'))",  # subquery
    ],
)
def test_rejects_file_functions_in_obfuscated_forms(sql):
    with pytest.raises(SqlNotAllowed):
        guard_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        # Columns/tables named like a denylisted function are fine — only calls match.
        "SELECT url, file, text FROM files",
        "SELECT t.url FROM urls t WHERE t.file IS NOT NULL",
        "SELECT count(*) FROM files WHERE url LIKE 'http%'",
        "SELECT max(text), sum(s3), avg(cluster) FROM metrics",
        "SELECT * FROM t WHERE file IN (1, 2, 3)",
        "SELECT read_text FROM staging",  # column that shares a blocked name
        'SELECT "url", "file" FROM "files"',  # quoted identifiers, still not calls
        "SELECT url_decode(u) AS u FROM t",  # different function, blocked name is a prefix
        "SELECT my_read_text(c) FROM t",  # blocked name is a suffix
        "SELECT read_text_of(c) FROM t",  # blocked name is a prefix
        # The name only appears inside a literal / comment — literal stripping must
        # not produce a false positive.
        "SELECT * FROM t WHERE note = 'read_text(''/etc/hostname'')'",
        "SELECT * FROM t WHERE note = 'load_file(/etc/passwd)'",
        "SELECT * FROM t -- read_text('/etc/hostname')",
        "SELECT /* pg_read_file('/etc/passwd') */ * FROM t",
        "SELECT $$url('http://evil.example')$$ AS note",
        "SELECT 'openrowset(' || name AS label FROM t",
    ],
)
def test_allows_lookalike_identifiers_and_literals(sql):
    assert guard_sql(sql)


# --- DuckDB replacement scans: a quoted path in table position (#267) ---
# No function call is involved, so the function denylist above cannot see these.


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM '/tmp/secret.csv'",
        "SELECT * FROM 'C:/creds.json'",
        "select * from '/tmp/a.parquet'",  # lower case
        "SELECT * FROM\n\t'/tmp/secret.csv'",  # newline/tab before the literal
        "SELECT * FROM /* sneaky */ '/tmp/secret.csv'",  # comment before the literal
        "WITH x AS (SELECT * FROM '/tmp/a.parquet') SELECT * FROM x",  # inside a CTE
        "SELECT * FROM t WHERE a IN (SELECT * FROM '/tmp/secret.csv')",  # subquery
        "SELECT * FROM orders JOIN '/tmp/secret.csv' s ON 1 = 1",  # join position
        "SELECT * FROM orders LEFT JOIN '/tmp/secret.csv' ON 1 = 1",
        "SELECT * FROM orders, '/tmp/secret.csv'",  # comma-separated FROM list
        "SELECT * FROM orders o, customers c, '/tmp/secret.csv'",
        "SELECT * FROM orders AS o, '/tmp/secret.csv'",
        "SELECT * FROM 'https://evil.example/x.csv'",  # network replacement scan
        # A table item is not always a bare word. Each of these hid the replacement
        # scan behind an item the comma-list prefix could not count.
        "SELECT * FROM \"orders\", '/tmp/secret.csv'",  # quoted identifier
        "SELECT * FROM [orders], '/tmp/secret.csv'",  # bracket-quoted (SQL Server)
        "SELECT * FROM `orders`, '/tmp/secret.csv'",  # backtick-quoted (MySQL)
        "SELECT * FROM\"orders\",'/tmp/secret.csv'",  # ...and with no whitespace at all
        "SELECT * FROM[orders],'/tmp/secret.csv'",
        "SELECT * FROM`orders`,'/tmp/secret.csv'",
        "SELECT * FROM (VALUES (1)) v, '/tmp/secret.csv'",  # parenthesised, nested
        "SELECT * FROM (SELECT * FROM (SELECT 1) a) b, '/tmp/secret.csv'",
        "SELECT * FROM (SELECT 1) s (a), '/tmp/secret.csv'",  # alias column list
        "SELECT * FROM orders AS o (a, b), '/tmp/secret.csv'",
        "SELECT * FROM a JOIN b USING (id), '/tmp/secret.csv'",
        "SELECT * FROM a NATURAL JOIN b, '/tmp/secret.csv'",
        # A join condition sits between the item and the comma.
        "SELECT * FROM orders o JOIN customers c ON o.id = c.id, '/tmp/secret.csv'",
        # Prefixed string literals are still string literals.
        "SELECT * FROM t, E'/tmp/secret.csv'",
        "SELECT * FROM t, N'/tmp/secret.csv'",
        "SELECT * FROM t, U&'/tmp/secret.csv'",
        "SELECT * FROM t, _utf8'/tmp/secret.csv'",
        "SELECT * FROM E'/tmp/secret.csv'",
        "SELECT * FROM'/tmp/secret.csv'",  # no whitespace before the literal
        "SELECT * FROM t,'/tmp/secret.csv'",
        "SELECT * FROM t,E'/tmp/secret.csv'",
        # Table functions and modifiers do not end the item list either.
        "SELECT * FROM generate_series(1, 3) g, '/tmp/secret.csv'",
        "SELECT * FROM t TABLESAMPLE BERNOULLI (10), '/tmp/secret.csv'",
    ],
)
def test_rejects_string_literal_in_table_position(sql):
    with pytest.raises(SqlNotAllowed) as excinfo:
        guard_sql(sql)
    assert "table position" in str(excinfo.value)


def test_rejects_string_literal_after_a_long_from_list():
    """The item list must not be walked past by padding it out — the old pattern
    stopped counting after 32 items and then allowed the 33rd."""
    tables = ", ".join(f"t{i}" for i in range(64))
    with pytest.raises(SqlNotAllowed) as excinfo:
        guard_sql(f"SELECT * FROM {tables}, '/tmp/secret.csv'")
    assert "table position" in str(excinfo.value)


def test_guard_sql_stays_linear_in_exempted_from_expressions():
    """REG-2: the enclosing-call lookup used to rebuild the paren stack from position
    0 for every match, making guard_sql quadratic — this query took ~30s."""
    sql = "SELECT " + ", ".join("SUBSTRING(c FROM '1')" for _ in range(4000)) + " FROM t"
    start = time.perf_counter()
    assert guard_sql(sql)
    assert time.perf_counter() - start < 1.0


def test_rejects_glob_directory_listing():
    with pytest.raises(SqlNotAllowed) as excinfo:
        guard_sql("SELECT * FROM glob('/tmp/*')")
    assert "GLOB()" in str(excinfo.value)


@pytest.mark.parametrize(
    "sql",
    [
        # FROM inside a function argument list is standard SQL, not a table reference.
        "SELECT EXTRACT(YEAR FROM '2024-01-01') AS y",
        "SELECT extract(epoch from '2024-01-01') FROM t",
        "SELECT EXTRACT(MONTH FROM '2024-01-01'), EXTRACT(DAY FROM '2024-01-01') FROM t",
        "SELECT SUBSTRING(x FROM 'a.*z') FROM t",  # Postgres regex substring
        "SELECT substring(name from '[0-9]+') AS digits FROM t",
        "SELECT TRIM(BOTH FROM '  padded  ') AS trimmed",
        "SELECT * FROM t WHERE EXTRACT(YEAR FROM '2024-06-01') = 2024",
        # Literals elsewhere in the query are untouched.
        "SELECT * FROM t WHERE note = 'from ''here'''",
        "SELECT * FROM t WHERE label IN ('a', 'b', 'c')",
        "SELECT * FROM t -- SELECT * FROM '/tmp/secret.csv'",
        "SELECT /* SELECT * FROM '/tmp/x' */ * FROM t",
        "SELECT * FROM t GROUP BY a HAVING max(b) > 'x'",
        "SELECT * FROM t ORDER BY a, 'x'",
        "SELECT * FROM t JOIN u ON u.code = 'ok'",
        "SELECT * FROM t, u WHERE t.code = 'ok'",
        # SQLite's GLOB *operator* is not a call and must stay legal.
        "SELECT * FROM t WHERE name GLOB 'a*'",
        "SELECT * FROM t WHERE name NOT GLOB '*.tmp'",
        # REG-1: `from`/`join` are the PREFIX of everyday column names. Matching them
        # without a trailing word boundary made the backstop accuse ordinary analyst
        # SQL of path traversal, and guard_sql sits on the workbench / saved-query /
        # dashboard-SQL / LLM-SQL / custom_sql hot path.
        "SELECT COALESCE(from_date, 'unknown') AS d FROM invoices",
        "SELECT NULLIF(join_key, '') FROM edges",
        "SELECT joined_at, 'active' AS status FROM users",
        "SELECT * FROM t ORDER BY from_date, 'x'",
        "SELECT COALESCE(to_date, 'n/a') AS d FROM periods",
        "SELECT from_currency, 'USD' AS target FROM fx_rates",
        "SELECT from_account, to_account, 'transfer' AS kind FROM ledger",
        "SELECT * FROM t GROUP BY from_date, 'x'",
        "SELECT * FROM t ORDER BY joined_at DESC, 'x'",
        "SELECT CASE WHEN from_date IS NULL THEN 'missing' ELSE 'ok' END FROM contracts",
        # A join condition is part of the FROM clause; the literal after it is not a
        # table item, and neither is one in the ORDER BY that follows.
        "SELECT * FROM a JOIN b ON a.x = b.x AND b.s = 'q' ORDER BY a.id, 'x'",
        "SELECT * FROM t o JOIN u USING (id) WHERE o.k = 'x'",
        "SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS v (id, name)",
    ],
)
def test_allows_from_in_function_arguments_and_ordinary_literals(sql):
    assert guard_sql(sql)


# --- quoted identifiers that merely CONTAIN a denied name (SEC-3 regression) ---


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT "URL (raw)" FROM pages',
        'SELECT "File (path)" FROM t',
        "SELECT [Cluster (k)] FROM t",
        "SELECT `remote (flag)` FROM t",
        'SELECT * FROM t WHERE "s3 (bucket)" IS NOT NULL',
        'SELECT "url (id)", "file (name)" FROM t',
        'SELECT sum("s3 (gb)") AS total FROM usage',
        # CTE / table-alias column lists: unquoted name immediately followed by "(".
        "WITH url (id, addr) AS (SELECT 1, 2) SELECT * FROM url",
        "WITH a AS (SELECT 1), url (id) AS (SELECT 2) SELECT * FROM url",
        "WITH RECURSIVE file (n) AS (SELECT 1) SELECT * FROM file",
        "SELECT * FROM (SELECT 1) AS file (a)",
        "SELECT * FROM (SELECT 1, 2) AS remote (a, b)",
        "SELECT * FROM orders o JOIN (SELECT 1) AS s3 (x) ON 1 = 1",
    ],
)
def test_allows_quoted_and_alias_names_that_look_like_functions(sql):
    assert guard_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        # The bare-word rule must not weaken denial of genuinely hidden calls.
        "SELECT \"pg_read_file\"('/etc/passwd')",
        "SELECT `load_file`('/etc/passwd')",
        "SELECT [readfile]('/etc/passwd')",
        # An alias-shaped name whose arguments are NOT a plain column list is a call.
        "SELECT * FROM t AS x, url('http://evil.example/x.csv')",
        "WITH x AS (SELECT 1) SELECT * FROM file('/etc/passwd', 'LineAsString')",
    ],
)
def test_alias_exemption_does_not_weaken_real_calls(sql):
    with pytest.raises(SqlNotAllowed):
        guard_sql(sql)


def test_function_denial_message_names_the_function():
    with pytest.raises(SqlNotAllowed) as excinfo:
        guard_sql("SELECT read_text('/etc/hostname')")
    assert str(excinfo.value) == "Function not allowed in read-only queries: READ_TEXT()"


def test_keyword_denial_message_style_is_unchanged():
    with pytest.raises(SqlNotAllowed) as excinfo:
        guard_sql("WITH x AS (SELECT 1) UPDATE t SET a = 1")
    assert str(excinfo.value) == "Keyword not allowed in read-only queries: UPDATE"
