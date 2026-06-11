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
    assert _opts("duckdb", "duckdb:///x.duckdb") == {"connect_args": {"read_only": True}}


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
