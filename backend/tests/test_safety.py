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


def test_function_denial_message_names_the_function():
    with pytest.raises(SqlNotAllowed) as excinfo:
        guard_sql("SELECT read_text('/etc/hostname')")
    assert str(excinfo.value) == "Function not allowed in read-only queries: READ_TEXT()"


def test_keyword_denial_message_style_is_unchanged():
    with pytest.raises(SqlNotAllowed) as excinfo:
        guard_sql("WITH x AS (SELECT 1) UPDATE t SET a = 1")
    assert str(excinfo.value) == "Keyword not allowed in read-only queries: UPDATE"
