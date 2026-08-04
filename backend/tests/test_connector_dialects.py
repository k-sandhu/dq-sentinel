"""Dialect registry + optional-driver behavior (issue #29).

Everything here must pass on a machine with NONE of the optional drivers
installed — no test connects to a real mysql/mssql/snowflake/bigquery/trino/
clickhouse server, and engine-option/DDL builders are asserted as pure dicts.
"""

import pytest
from sqlalchemy.engine import make_url

from app.connectors.dialects import (
    REGISTRY,
    SPEC_BY_SCHEME,
    DriverNotInstalled,
    driver_installed,
    missing_driver_message,
)
from app.connectors.sa import ALLOWED_SCHEMES, Connector, kind_from_dsn
from app.connectors.safety import SqlNotAllowed, guard_sql

EXPECTED_KINDS = {
    "sqlite",
    "duckdb",
    "postgresql",
    "mysql",
    "mssql",
    "snowflake",
    "bigquery",
    "trino",
    "clickhouse",
}


def _quote(ident: str) -> str:
    """Stand-in for Connector.quote so DDL builders run without any engine."""
    return f'"{ident}"'


# ---- registry shape ----
def test_registry_completeness():
    assert set(REGISTRY) == EXPECTED_KINDS
    for spec in REGISTRY.values():
        assert spec.label, spec.kind
        assert spec.dsn_example, spec.kind
        assert spec.notes, spec.kind
        assert spec.schemes, spec.kind
        # an extra implies a probe-able driver module and vice versa
        assert (spec.install_extra is None) == (spec.driver_import is None), spec.kind


def test_no_scheme_overlaps():
    seen: dict[str, str] = {}
    for spec in REGISTRY.values():
        for scheme in spec.schemes:
            assert scheme not in seen, f"'{scheme}' claimed by {seen[scheme]} and {spec.kind}"
            seen[scheme] = spec.kind
    assert set(seen) == set(ALLOWED_SCHEMES) == set(SPEC_BY_SCHEME)


def test_default_drivers():
    assert REGISTRY["mysql"].default_driver == "pymysql"
    assert REGISTRY["mssql"].default_driver == "pyodbc"
    assert REGISTRY["clickhouse"].default_driver == "native"


# ---- DSN parsing (works without any driver installed) ----
@pytest.mark.parametrize(
    ("dsn", "kind"),
    [
        ("sqlite:///x.db", "sqlite"),
        ("duckdb:///x.duckdb", "duckdb"),
        ("postgresql://u@h/db", "postgresql"),
        ("postgresql+psycopg://u@h/db", "postgresql"),
        ("postgresql+psycopg2://u@h/db", "postgresql"),
        ("mysql://u@h/db", "mysql"),
        ("mysql+pymysql://u@h/db", "mysql"),
        ("mariadb://u@h/db", "mysql"),
        ("mariadb+pymysql://u@h/db", "mysql"),
        ("mssql://u@h/db", "mssql"),
        ("mssql+pyodbc://u@h/db?driver=ODBC+Driver+18+for+SQL+Server", "mssql"),
        ("mssql+pymssql://u@h/db", "mssql"),
        ("snowflake://u:p@acct/db/schema?warehouse=WH", "snowflake"),
        ("bigquery://project-id/dataset_name", "bigquery"),
        ("trino://u@h:8080/catalog/schema", "trino"),
        ("clickhouse://u@h/db", "clickhouse"),
        ("clickhouse+native://u@h:9000/db", "clickhouse"),
        ("clickhouse+http://u@h:8123/db", "clickhouse"),
    ],
)
def test_kind_from_dsn_accepts_supported(dsn, kind):
    assert kind_from_dsn(dsn) == kind


@pytest.mark.parametrize("dsn", ["mongodb://u@h/db", "oracle://u@h/db"])
def test_kind_from_dsn_rejects_unsupported(dsn):
    with pytest.raises(SqlNotAllowed) as ei:
        kind_from_dsn(dsn)
    assert "snowflake" in str(ei.value)  # message lists the supported kinds


# ---- engine options (pure dict assertions; no driver import happens) ----
def _opts(kind: str, dsn: str) -> dict:
    return REGISTRY[kind].engine_options(make_url(dsn))


def test_engine_options_sqlite_duckdb():
    assert _opts("sqlite", "sqlite:///x.db") == {"connect_args": {"check_same_thread": False}}
    # read_only alone stops writes but NOT DuckDB's replacement scan / glob() host-file
    # reads (#267); enable_external_access=false is the engine-level kill switch.
    assert _opts("duckdb", "duckdb:///x.duckdb") == {
        "connect_args": {"read_only": True, "config": {"enable_external_access": "false"}}
    }
    assert "enable_external_access" in REGISTRY["duckdb"].notes


def test_engine_options_postgresql():
    opts = _opts("postgresql", "postgresql://u@h/db")
    assert opts["pool_pre_ping"] is True
    assert opts["pool_size"] == 5
    assert opts["max_overflow"] == 5
    assert opts["connect_args"]["options"] == (
        "-c default_transaction_read_only=on -c statement_timeout=30000"
    )


def test_engine_options_mysql_session_read_only():
    opts = _opts("mysql", "mysql://u@h/db")
    assert opts["pool_pre_ping"] is True
    assert opts["pool_size"] == 5
    assert opts["max_overflow"] == 5
    assert opts["connect_args"]["init_command"] == "SET SESSION TRANSACTION READ ONLY"


def test_engine_options_mssql_snowflake():
    assert _opts("mssql", "mssql://u@h/db") == {"pool_pre_ping": True}
    assert _opts("snowflake", "snowflake://u:p@acct/db/schema") == {"pool_pre_ping": True}


def test_engine_options_bigquery_trino():
    assert _opts("bigquery", "bigquery://project-id/dataset_name") == {}
    assert _opts("trino", "trino://u@h:8080/catalog/schema") == {}


def test_engine_options_clickhouse_readonly():
    opts = _opts("clickhouse", "clickhouse://u@h/db")
    assert opts == {"connect_args": {"settings": {"readonly": 1}}}


# ---- schema iteration config ----
def test_multi_schema_and_system_schemas():
    assert REGISTRY["sqlite"].multi_schema is False
    assert REGISTRY["duckdb"].multi_schema is False
    assert REGISTRY["mysql"].multi_schema is False  # the DSN names the database
    assert REGISTRY["postgresql"].system_schemas == {"information_schema", "pg_catalog"}
    assert REGISTRY["mssql"].system_schemas == {"INFORMATION_SCHEMA", "sys"}
    assert REGISTRY["snowflake"].system_schemas == {"INFORMATION_SCHEMA"}
    assert REGISTRY["trino"].system_schemas == {"information_schema"}
    assert REGISTRY["clickhouse"].system_schemas == {"system", "INFORMATION_SCHEMA", "information_schema"}
    assert REGISTRY["bigquery"].multi_schema is True
    assert REGISTRY["bigquery"].system_schemas == frozenset()


# ---- DDL catalog queries ----
def test_ddl_catalog_queries_pass_guard_sql():
    covered = set()
    for spec in REGISTRY.values():
        if spec.ddl_queries is None:
            continue
        covered.add(spec.kind)
        for schema in ("analytics", None):
            for sql, params in spec.ddl_queries("orders", schema, _quote):
                guard_sql(sql)  # raises SqlNotAllowed on anything unsafe
                assert sql.count(";") == 0  # single statement
                assert isinstance(params, dict)
    assert covered == {"mysql", "mssql", "snowflake", "bigquery", "trino", "clickhouse"}


def test_ddl_query_shapes():
    assert len(REGISTRY["snowflake"].ddl_queries("t", "s", _quote)) == 2  # VIEW then TABLE fallback
    (sql, params), = REGISTRY["mssql"].ddl_queries("orders", "dbo", _quote)
    assert params == {"qual": "dbo.orders"}
    (sql, params), = REGISTRY["mssql"].ddl_queries("orders", None, _quote)
    assert params == {"qual": "orders"}
    # bigquery quotes the dataset as an identifier and binds the table name
    (sql, params), = REGISTRY["bigquery"].ddl_queries("orders", "my_dataset", _quote)
    assert '"my_dataset".INFORMATION_SCHEMA.TABLES' in sql
    assert params == {"t": "orders"}
    assert REGISTRY["bigquery"].ddl_queries("orders", None, _quote) == []


# ---- optional drivers ----
def test_driver_installed_flags():
    assert driver_installed(REGISTRY["sqlite"]) is True  # stdlib
    assert driver_installed(REGISTRY["duckdb"]) is True  # core dependency
    assert driver_installed(REGISTRY["snowflake"]) is False  # optional extra, not on this machine


def test_missing_driver_raises_driver_not_installed():
    with pytest.raises(DriverNotInstalled) as ei:
        Connector("snowflake://u:p@acct/db/schema")
    msg = str(ei.value)
    assert msg == missing_driver_message(REGISTRY["snowflake"])
    assert 'pip install "dqsentinel[snowflake]"' in msg


def test_bare_mysql_scheme_defaults_to_pymysql():
    # Without the +pymysql default, SQLAlchemy would look for MySQLdb instead.
    with pytest.raises(DriverNotInstalled) as ei:
        Connector("mysql://u:p@h/db")
    assert 'dqsentinel[mysql]' in str(ei.value)


# ---- DuckDB host-file access is off at the ENGINE level (#267/#281) ----
# These bypass guard_sql on purpose: the point is that the connection itself refuses,
# so a gap in the regex guard is not a host-file read. duckdb is a core dependency.


def _seed_duckdb(tmp_path, *, external_view: bool = False) -> tuple[str, str]:
    """Build a .duckdb file with a secret CSV beside it; returns (db, secret) paths.

    With ``external_view`` the catalog also holds a view whose *definition* reads that
    CSV — an object that only resolves when external access is on.
    """
    import duckdb

    secret = tmp_path / "secret.csv"
    secret.write_text("col_a,col_b\nSUPER,SECRET\n", encoding="utf-8")
    db = tmp_path / "analytics.duckdb"
    con = duckdb.connect(str(db))  # writer fully closed before the connector opens it
    try:
        con.execute("CREATE TABLE orders (id INTEGER, amount DOUBLE)")
        con.execute("INSERT INTO orders VALUES (1, 10.5), (2, 20.0)")
        con.execute("CREATE VIEW orders_v AS SELECT * FROM orders")
        if external_view:
            con.execute(
                f"CREATE VIEW lake_v AS SELECT * FROM read_csv_auto('{secret.as_posix()}')"
            )
    finally:
        con.close()
    return db.as_posix(), secret.as_posix()


@pytest.fixture
def duckdb_connector(tmp_path):
    """A read-only Connector over a real .duckdb file, plus a secret file beside it."""
    db, secret = _seed_duckdb(tmp_path)
    return Connector(f"duckdb:///{db}"), secret, tmp_path.as_posix()


def test_duckdb_still_reads_the_database_file(duckdb_connector):
    """Disabling external access must NOT break opening/reading the .duckdb file:
    the curated catalog seeds one, and data/download_public_data.py builds one."""
    connector, _secret, _dir = duckdb_connector
    assert connector.run_select("SELECT * FROM orders").rows == [[1, 10.5], [2, 20.0]]
    assert connector.run_select("SELECT * FROM orders_v").rows == [[1, 10.5], [2, 20.0]]
    assert connector.row_count("orders") == 2
    assert [t["table_name"] for t in connector.list_tables()] == ["orders", "orders_v"]
    assert [c["name"] for c in connector.get_columns("orders")] == ["id", "amount"]
    # duckdb_views()/duckdb_tables() catalog reads still resolve real DDL
    assert connector.get_ddl("orders_v")[1] == "database"
    assert connector.get_ddl("orders")[1] == "database"


@pytest.mark.parametrize(
    "template",
    [
        "SELECT * FROM '{secret}'",  # replacement scan
        "WITH x AS (SELECT * FROM '{secret}') SELECT * FROM x",
        "SELECT * FROM orders o, '{secret}' s",
        "SELECT * FROM orders JOIN '{secret}' s ON 1 = 1",
        "SELECT * FROM (FROM '{secret}')",  # DuckDB FROM-first syntax
        "SELECT (SELECT count(*) FROM '{secret}') AS n",
        "SELECT * FROM read_csv('{secret}')",
        "SELECT * FROM read_csv_auto('{secret}')",
        "SELECT * FROM read_text('{secret}')",
        "SELECT * FROM read_blob('{secret}')",  # the byte-level read (#267)
        "SELECT * FROM read_parquet('{secret}')",
        "SELECT * FROM parquet_scan('{secret}')",
        "SELECT * FROM read_json_auto('{secret}')",
        "SELECT * FROM sniff_csv('{secret}')",
        # Outbound network, not just local files: SSRF at the cloud metadata endpoint
        # is the same capability and the same switch turns it off.
        "SELECT * FROM read_json_auto('http://169.254.169.254/latest/meta-data')",
        "SELECT * FROM 'https://evil.example/x.csv'",
    ],
)
def test_duckdb_engine_refuses_host_file_reads(duckdb_connector, template):
    from sqlalchemy import text

    connector, secret, _dir = duckdb_connector
    with connector.engine.connect() as conn:  # deliberately NOT via guard_sql
        with pytest.raises(Exception) as ei:  # noqa: PT011 - driver-specific type
            conn.execute(text(template.format(secret=secret))).fetchall()
    assert "Permission Error" in str(ei.value)
    assert "SECRET" not in str(ei.value)  # the payload must not come back in the error


def test_duckdb_engine_refuses_a_replacement_scan_spelled_as_a_quoted_identifier(duckdb_connector):
    """A path in DOUBLE QUOTES is still a replacement scan to DuckDB.

    guard_sql() masks quoted identifiers before looking for a literal in table
    position, so this spelling reaches the driver — verified: with the pre-#296
    connect_args (read_only only) it returns the CSV's contents. It is the
    engine-level kill switch, not the regex backstop, that refuses it. Asserting the
    ENGINE refuses keeps this honest and stays true if the guard later learns the
    spelling too.
    """
    from sqlalchemy import text

    connector, secret, _dir = duckdb_connector
    for sql in (
        f'SELECT * FROM "{secret}"',
        f'SELECT * FROM orders o, "{secret}" s',
    ):
        assert guard_sql(sql)  # documents the layer that does NOT catch it
        with connector.engine.connect() as conn:
            with pytest.raises(Exception) as ei:  # noqa: PT011 - driver-specific type
                conn.execute(text(sql)).fetchall()
        assert "Permission Error" in str(ei.value)


@pytest.mark.parametrize(
    "statement",
    [
        # ATTACH is the cross-connection theft vector; both the native and the
        # sqlite/postgres attachers are refused (the latter needs an extension load,
        # which the same switch disables).
        "ATTACH '{other}' AS stolen (READ_ONLY)",
        "ATTACH '{other}' AS stolen (TYPE SQLITE)",
        # COPY writes the source out to a host path.
        "COPY orders TO '{dir}/exfil.csv'",
        "COPY (SELECT * FROM orders) TO '{dir}/exfil.parquet'",
        # Extension install/load would re-add every scanner the denylist removes.
        "INSTALL httpfs",
        "LOAD httpfs",
        "INSTALL sqlite_scanner",
    ],
)
def test_duckdb_engine_refuses_attach_copy_and_extension_loading(tmp_path, statement):
    """#267 asked for ATTACH to be audited. On DuckDB the driver refuses it outright,
    so guard_sql's keyword denylist is a backstop rather than the only defence."""
    db, _secret = _seed_duckdb(tmp_path)
    other = tmp_path / "other_connection.duckdb"
    import duckdb

    con = duckdb.connect(str(other))
    try:
        con.execute("CREATE TABLE creds AS SELECT 'root' AS u, 'CROSSCONNSECRET' AS p")
    finally:
        con.close()

    connector = Connector(f"duckdb:///{db}")
    try:
        with connector.engine.connect() as conn:  # deliberately NOT via guard_sql
            with pytest.raises(Exception) as ei:  # noqa: PT011 - driver-specific type
                conn.exec_driver_sql(
                    statement.format(other=other.as_posix(), dir=tmp_path.as_posix())
                )
        assert "Permission Error" in str(ei.value)
    finally:
        connector.engine.dispose()


def test_duckdb_cannot_read_another_connections_backing_file(tmp_path):
    """The cross-connection data-theft scenario #267 describes: one DuckDB source
    reaching the raw database file behind a DIFFERENT connection. Every payload is
    driven through Connector.run_select, i.e. exactly what /query/run, custom_sql
    checks and the LLM agents do."""
    db, _secret = _seed_duckdb(tmp_path)

    victim = tmp_path / "other_connection.sqlite"
    import sqlite3

    con = sqlite3.connect(str(victim))
    try:
        con.execute("CREATE TABLE connections (name TEXT, dsn TEXT)")
        con.execute("INSERT INTO connections VALUES ('prod', 'CROSSCONNSECRET')")
        con.commit()
    finally:
        con.close()
    assert "CROSSCONNSECRET" in victim.read_bytes().decode("latin-1")  # it IS in the file

    path = victim.as_posix()
    connector = Connector(f"duckdb:///{db}")
    try:
        for sql in (
            f"SELECT * FROM read_blob('{path}')",
            f"SELECT content FROM read_text('{path}')",
            f"SELECT * FROM '{path}'",
            f'SELECT * FROM "{path}"',
            f"SELECT * FROM sqlite_scan('{path}', 'connections')",
            f"SELECT * FROM sqlite_query('{path}', 'select dsn from connections')",
            f"SELECT * FROM glob('{tmp_path.as_posix()}/*')",
        ):
            with pytest.raises(Exception) as ei:  # noqa: PT011 - guard or driver
                connector.run_select(sql, limit=10)
            assert "CROSSCONNSECRET" not in str(ei.value)
    finally:
        connector.engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "SET enable_external_access=true",
        "SET GLOBAL enable_external_access=true",
        "PRAGMA enable_external_access=true",
    ],
)
def test_duckdb_external_access_cannot_be_turned_on_mid_session(duckdb_connector, statement):
    """The switch is a startup-time setting: even bypassing guard_sql entirely, a
    session cannot hand itself the capability back."""
    from sqlalchemy import text

    connector, _secret, _dir = duckdb_connector
    with connector.engine.connect() as conn:  # a failed SET aborts the transaction
        with pytest.raises(Exception) as ei:  # noqa: PT011 - driver-specific type
            conn.exec_driver_sql(statement)
    assert "Cannot enable external access" in str(ei.value)
    with connector.engine.connect() as conn:
        assert conn.execute(text("SELECT current_setting('enable_external_access')")).scalar() is False


def test_duckdb_engine_refuses_directory_listing(duckdb_connector):
    from sqlalchemy import text

    connector, _secret, directory = duckdb_connector
    with connector.engine.connect() as conn:  # deliberately NOT via guard_sql
        with pytest.raises(Exception) as ei:  # noqa: PT011 - driver-specific type
            conn.execute(text(f"SELECT * FROM glob('{directory}/*')")).fetchall()
    assert "Permission Error" in str(ei.value)


def test_duckdb_external_access_setting_is_off(duckdb_connector):
    from sqlalchemy import text

    connector, _secret, _dir = duckdb_connector
    with connector.engine.connect() as conn:
        assert conn.execute(text("SELECT current_setting('enable_external_access')")).scalar() is False


def test_duckdb_external_access_cannot_be_re_enabled_from_the_dsn(tmp_path):
    """duckdb-engine merges the DSN query string into DuckDB's config AFTER our
    connect_args, so `?enable_external_access=true` used to hand host-file reads back
    to whoever authored the connection (#267). sa.py strips that surface."""
    from sqlalchemy import text

    db, secret = _seed_duckdb(tmp_path)
    connector = Connector(f"duckdb:///{db}?enable_external_access=true")
    try:
        with connector.engine.connect() as conn:
            setting = conn.execute(text("SELECT current_setting('enable_external_access')"))
            assert setting.scalar() is False
            with pytest.raises(Exception) as ei:  # noqa: PT011 - driver-specific type
                conn.execute(text(f"SELECT * FROM '{secret}'")).fetchall()
        assert "Permission Error" in str(ei.value)
        # ...and the database file itself still opens and reads normally.
        assert connector.run_select("SELECT * FROM orders").rows == [[1, 10.5], [2, 20.0]]
    finally:
        connector.engine.dispose()


@pytest.mark.parametrize(
    "query",
    [
        "enable_external_access=true",
        "enable_external_access=True",  # duckdb-engine lower-cases nothing for us
        "ENABLE_EXTERNAL_ACCESS=true",
        "enable_external_access=1",
        "allow_unsigned_extensions=true&enable_external_access=true",
        "memory_limit=1GB&enable_external_access=true",
        "config=%7B%27enable_external_access%27%3A%27true%27%7D",  # config={'...':'...'}
    ],
)
def test_duckdb_dsn_config_surface_is_stripped_in_every_spelling(tmp_path, query):
    """sa.py drops the WHOLE query string rather than denylisting one key, so an
    admin authoring a connection cannot spell the override differently and win."""
    from sqlalchemy import text

    db, _secret = _seed_duckdb(tmp_path)
    connector = Connector(f"duckdb:///{db}?{query}")
    try:
        with connector.engine.connect() as conn:
            setting = conn.execute(text("SELECT current_setting('enable_external_access')"))
            assert setting.scalar() is False
    finally:
        connector.engine.dispose()


def test_duckdb_refuses_catalog_objects_backed_by_external_files(tmp_path):
    """CMP-1, the documented cost of the kill switch: a view DEFINED over an external
    file cannot be read either, so a DuckDB catalog that fronts a lake is unusable
    here. Locked in deliberately — a per-connection opt-out would reopen #267."""
    db, _secret = _seed_duckdb(tmp_path, external_view=True)
    connector = Connector(f"duckdb:///{db}")
    try:
        # ordinary tables and views over them are unaffected
        assert connector.run_select("SELECT * FROM orders").rows == [[1, 10.5], [2, 20.0]]
        assert connector.run_select("SELECT * FROM orders_v").rows == [[1, 10.5], [2, 20.0]]
        with pytest.raises(Exception) as ei:  # noqa: PT011 - driver-specific type
            connector.run_select("SELECT * FROM lake_v")
        assert "Permission Error" in str(ei.value)
    finally:
        connector.engine.dispose()
    # ...and the limitation is stated where admins choosing an engine will read it,
    # naming the construct that stops working rather than only the setting.
    notes = REGISTRY["duckdb"].notes
    assert "read_parquet" in notes
    assert "DSN query parameters are dropped" in notes


# ---- SQLite: the ATTACH audit #267 asked for -------------------------------
# AUDIT RESULT (verified against this driver, not assumed): a SQLite source opened
# the way sa.py opens it — file:...?mode=ro&uri=true — will still ATTACH an arbitrary
# database file and read it. mode=ro only makes the attachment read-only. There is no
# SQLite equivalent of DuckDB's enable_external_access, so DuckDB's "the driver
# refuses, the regex is a backstop" ordering is INVERTED here: everything stopping
# cross-connection reads on SQLite lives in guard_sql — the SELECT/WITH-only rule,
# the `attach` keyword denylist, and the multi-statement rule (with sqlite3's own
# one-statement-per-execute as an accidental fourth). Three thin layers, none of them
# a driver permission.
#
# These tests pin the app-level property — no connector API lets a query reach
# another database file — rather than the driver's permissiveness, so adding a
# driver-level control (e.g. sqlite3 set_authorizer denying SQLITE_ATTACH) later
# strengthens the picture without breaking them.


def _seed_sqlite_pair(tmp_path) -> tuple[str, str]:
    """(source db, another connection's db holding a marker row)."""
    import sqlite3

    src = tmp_path / "shop.sqlite"
    con = sqlite3.connect(str(src))
    try:
        con.execute("CREATE TABLE orders (id INTEGER, amount REAL)")
        con.execute("INSERT INTO orders VALUES (1, 10.5)")
        con.commit()
    finally:
        con.close()

    other = tmp_path / "other_connection.sqlite"
    con = sqlite3.connect(str(other))
    try:
        con.execute("CREATE TABLE connections (name TEXT, dsn TEXT)")
        con.execute("INSERT INTO connections VALUES ('prod', 'CROSSCONNSECRET')")
        con.commit()
    finally:
        con.close()
    return src.as_posix(), other.as_posix()


def test_sqlite_connector_never_reaches_another_database_file(tmp_path):
    src, other = _seed_sqlite_pair(tmp_path)
    connector = Connector(f"sqlite:///{src}")
    try:
        assert connector.run_select("SELECT * FROM orders").rows == [[1, 10.5]]  # still usable
        for sql in (
            f"ATTACH DATABASE '{other}' AS stolen",
            f"attach database '{other}' as stolen",
            f"/* c */ ATTACH DATABASE '{other}' AS stolen",
            f"SELECT 1; ATTACH DATABASE '{other}' AS stolen",
            f"WITH q AS (SELECT 1) ATTACH DATABASE '{other}' AS stolen",
            f"SELECT * FROM read_blob('{other}')",
            f"SELECT readfile('{other}')",
            "SELECT * FROM stolen.connections",  # no attachment may survive on the pool
        ):
            with pytest.raises(Exception) as ei:  # noqa: PT011 - guard or driver
                connector.run_select(sql, limit=10)
            assert "CROSSCONNSECRET" not in str(ei.value)
    finally:
        connector.engine.dispose()


def test_sqlite_attach_is_rejected_by_each_guard_rule_independently(tmp_path):
    """Name the rule that refuses each spelling, so a change to any one of them shows
    up as this test rather than as a silent narrowing of the SQLite defence."""
    _src, other = _seed_sqlite_pair(tmp_path)
    for sql, message in (
        (
            f"ATTACH DATABASE '{other}' AS stolen",
            "Only SELECT / WITH queries are allowed",
        ),
        (
            f"WITH q AS (SELECT 1) ATTACH DATABASE '{other}' AS stolen",
            "Keyword not allowed in read-only queries: ATTACH",
        ),
        (
            f"SELECT 1; ATTACH DATABASE '{other}' AS stolen",
            "Multiple statements are not allowed",
        ),
    ):
        with pytest.raises(SqlNotAllowed) as ei:
            guard_sql(sql)
        assert str(ei.value) == message, sql


def test_sqlite_connection_is_read_only_at_the_driver_level(tmp_path):
    """The mode=ro rewrite in sa.py, exercised rather than asserted as a string."""
    src, _other = _seed_sqlite_pair(tmp_path)
    connector = Connector(f"sqlite:///{src}")
    try:
        with connector.engine.connect() as conn:  # deliberately NOT via guard_sql
            with pytest.raises(Exception) as ei:  # noqa: PT011 - driver-specific type
                conn.exec_driver_sql("INSERT INTO orders VALUES (99, 1.0)")
        assert "readonly database" in str(ei.value)
    finally:
        connector.engine.dispose()


# ---- API surface ----
def test_test_endpoint_reports_missing_driver_verbatim(client, admin_headers):
    resp = client.post(
        "/api/v1/connections/test",
        json={"name": "snow", "dsn": "snowflake://u:p@acct/db/schema"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["message"] == (
        "The Snowflake driver is not installed on this server. "
        'Install it with: pip install "dqsentinel[snowflake]"'
    )


def test_engines_endpoint(client, admin_headers):
    resp = client.get("/api/v1/connections/engines", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    engines = resp.json()
    assert len(engines) == 9
    by_kind = {e["kind"]: e for e in engines}
    assert set(by_kind) == EXPECTED_KINDS
    assert by_kind["sqlite"]["driver_installed"] is True
    assert by_kind["duckdb"]["driver_installed"] is True
    assert by_kind["snowflake"]["driver_installed"] is False
    assert by_kind["snowflake"]["install_extra"] == "snowflake"
    assert by_kind["sqlite"]["install_extra"] is None
    assert all(e["dsn_example"] and e["notes"] for e in engines)
    labels = [e["label"] for e in engines]
    assert labels == sorted(labels)  # sorted by label


def test_engines_endpoint_requires_auth(client):
    assert client.get("/api/v1/connections/engines").status_code == 401
