"""Declarative registry of supported source-database dialects (issue #29).

Each engine kind is described by a DialectSpec: which DSN schemes map to it, which
Python driver it needs (drivers beyond the bundled sqlite/duckdb are OPTIONAL
extras — the app must import and run with none of them installed), how to open it
as read-only as the engine allows, and how to fetch real object DDL from its
catalog. ``sa.py`` consumes this registry; nothing here imports a driver at
module import time — availability is probed with ``importlib.util.find_spec``.
"""

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import URL

# Builder returning create_engine() kwargs (incl. connect_args) for a parsed URL.
EngineOptionsBuilder = Callable[[URL], dict[str, Any]]

# (table, schema, quote) -> ordered catalog-query attempts as (sql, params).
# Every SQL string returned here is a single SELECT statement and is executed via
# Connector.scalar(), i.e. it passes guard_sql() before touching the source.
DdlBuilder = Callable[[str, str | None, Callable[[str], str]], list[tuple[str, dict[str, Any]]]]


class DriverNotInstalled(RuntimeError):
    """A supported dialect whose optional driver package is missing on this server."""


def _no_options(url: URL) -> dict[str, Any]:
    return {}


def _sqlite_options(url: URL) -> dict[str, Any]:
    # The mode=ro URI rewrite itself happens in sa.py (it changes the DSN, not
    # the kwargs); writes fail at the driver level once reopened read-only.
    return {"connect_args": {"check_same_thread": False}}


def _duckdb_options(url: URL) -> dict[str, Any]:
    return {"connect_args": {"read_only": True}}


def _postgresql_options(url: URL) -> dict[str, Any]:
    # Read-only transactions + a statement timeout so ad-hoc workbench/agent
    # queries can't camp on the source (issue #40).
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 5,
        "connect_args": {"options": "-c default_transaction_read_only=on -c statement_timeout=30000"},
    }


def _mysql_options(url: URL) -> dict[str, Any]:
    # ENFORCED: init_command runs once per connection and flips the session's
    # default transaction access mode to READ ONLY, so every statement
    # (autocommitted ones included) is rejected by MySQL/MariaDB with
    # "Cannot execute statement in a READ ONLY transaction".
    # NOT ENFORCED: a session could issue `SET SESSION TRANSACTION READ WRITE`
    # to undo it — guard_sql() blocks anything that is not a single SELECT/WITH
    # on every app path, but a read-only grant on the server is the backstop.
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 5,
        "connect_args": {"init_command": "SET SESSION TRANSACTION READ ONLY"},
    }


def _mssql_options(url: URL) -> dict[str, Any]:
    # SQL Server has no session-level read-only mode; see the registry notes.
    return {"pool_pre_ping": True}


def _snowflake_options(url: URL) -> dict[str, Any]:
    return {"pool_pre_ping": True}


def _clickhouse_options(url: URL) -> dict[str, Any]:
    # readonly=1 is a ClickHouse server-side setting forwarded with every query
    # by the driver; the server then rejects writes/DDL. Only takes effect when
    # the optional driver is installed (connect_args are passed through to it).
    return {"connect_args": {"settings": {"readonly": 1}}}


def _qual(table: str, schema: str | None) -> str:
    return f"{schema}.{table}" if schema else table


def _mysql_ddl(table: str, schema: str | None, quote: Callable[[str], str]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "SELECT view_definition FROM information_schema.views "
            "WHERE table_name = :t AND table_schema = COALESCE(:s, DATABASE())",
            {"t": table, "s": schema},
        )
    ]


def _mssql_ddl(table: str, schema: str | None, quote: Callable[[str], str]) -> list[tuple[str, dict[str, Any]]]:
    return [("SELECT OBJECT_DEFINITION(OBJECT_ID(:qual))", {"qual": _qual(table, schema)})]


def _snowflake_ddl(table: str, schema: str | None, quote: Callable[[str], str]) -> list[tuple[str, dict[str, Any]]]:
    qual = _qual(table, schema)
    return [
        ("SELECT GET_DDL('VIEW', :qual)", {"qual": qual}),
        ("SELECT GET_DDL('TABLE', :qual)", {"qual": qual}),
    ]


def _bigquery_ddl(table: str, schema: str | None, quote: Callable[[str], str]) -> list[tuple[str, dict[str, Any]]]:
    if not schema:  # INFORMATION_SCHEMA.TABLES lives per-dataset in BigQuery
        return []
    return [
        (
            f"SELECT ddl FROM {quote(schema)}.INFORMATION_SCHEMA.TABLES WHERE table_name = :t",
            {"t": table},
        )
    ]


def _trino_ddl(table: str, schema: str | None, quote: Callable[[str], str]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "SELECT view_definition FROM information_schema.views "
            "WHERE table_name = :t AND table_schema = COALESCE(:s, table_schema)",
            {"t": table, "s": schema},
        )
    ]


def _clickhouse_ddl(table: str, schema: str | None, quote: Callable[[str], str]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "SELECT create_table_query FROM system.tables "
            "WHERE name = :t AND database = COALESCE(:s, currentDatabase())",
            {"t": table, "s": schema},
        )
    ]


@dataclass(frozen=True)
class DialectSpec:
    kind: str
    label: str
    schemes: tuple[str, ...]  # accepted SQLAlchemy drivername values
    driver_import: str | None  # module for importlib.util.find_spec; None = stdlib/bundled
    install_extra: str | None  # pip extra, i.e. pip install "dqsentinel[<extra>]"
    dsn_example: str
    notes: str  # one-line read-only guarantees, shown to admins
    multi_schema: bool = False  # iterate inspector schemas vs. DSN-named schema only
    system_schemas: frozenset[str] = frozenset()  # excluded case-insensitively
    default_driver: str | None = None  # appended as +driver when the DSN uses the bare scheme
    engine_options: EngineOptionsBuilder = _no_options
    ddl_queries: DdlBuilder | None = None  # None = handled inline in sa.get_ddl (or synthesized)


REGISTRY: dict[str, DialectSpec] = {
    spec.kind: spec
    for spec in (
        DialectSpec(
            kind="sqlite",
            label="SQLite",
            schemes=("sqlite",),
            driver_import=None,
            install_extra=None,
            dsn_example="sqlite:///C:/data/shop.sqlite",
            notes="Opened via a mode=ro URI, so writes fail inside SQLite itself.",
            engine_options=_sqlite_options,
        ),
        DialectSpec(
            kind="duckdb",
            label="DuckDB",
            schemes=("duckdb",),
            driver_import=None,
            install_extra=None,
            dsn_example="duckdb:///C:/data/analytics.duckdb",
            notes="Opened with read_only=True, so DuckDB rejects writes at the driver level.",
            engine_options=_duckdb_options,
        ),
        DialectSpec(
            kind="postgresql",
            label="PostgreSQL",
            schemes=("postgresql", "postgresql+psycopg", "postgresql+psycopg2"),
            driver_import="psycopg",
            install_extra="postgres",
            dsn_example="postgresql+psycopg://user:pass@host:5432/dbname",
            notes="Sessions force default_transaction_read_only=on plus a 30s statement timeout.",
            multi_schema=True,
            system_schemas=frozenset({"information_schema", "pg_catalog"}),
            engine_options=_postgresql_options,
        ),
        DialectSpec(
            kind="mysql",
            label="MySQL / MariaDB",
            schemes=("mysql", "mysql+pymysql", "mariadb", "mariadb+pymysql"),
            driver_import="pymysql",
            install_extra="mysql",
            dsn_example="mysql+pymysql://user:pass@host:3306/dbname",
            notes=(
                "Sessions start READ ONLY (init_command); use a read-only grant "
                "as belt-and-braces."
            ),
            default_driver="pymysql",
            engine_options=_mysql_options,
            ddl_queries=_mysql_ddl,
        ),
        DialectSpec(
            kind="mssql",
            label="SQL Server",
            schemes=("mssql", "mssql+pyodbc", "mssql+pymssql"),
            driver_import="pyodbc",
            install_extra="mssql",
            dsn_example="mssql+pyodbc://user:pass@host:1433/dbname?driver=ODBC+Driver+18+for+SQL+Server",
            notes=(
                "SQL Server has no session read-only mode: protection is guard_sql plus a "
                "read-only login; for AG replicas add ApplicationIntent=ReadOnly to the DSN."
            ),
            multi_schema=True,
            system_schemas=frozenset({"INFORMATION_SCHEMA", "sys"}),
            default_driver="pyodbc",
            engine_options=_mssql_options,
            ddl_queries=_mssql_ddl,
        ),
        DialectSpec(
            kind="snowflake",
            label="Snowflake",
            schemes=("snowflake",),
            driver_import="snowflake.sqlalchemy",
            install_extra="snowflake",
            dsn_example="snowflake://user:pass@account/database/schema?warehouse=WH&role=ANALYST_RO",
            notes="Queries run through guard_sql; connect with a read-only role (USAGE + SELECT only).",
            multi_schema=True,
            system_schemas=frozenset({"INFORMATION_SCHEMA"}),
            engine_options=_snowflake_options,
            ddl_queries=_snowflake_ddl,
        ),
        DialectSpec(
            kind="bigquery",
            label="BigQuery",
            schemes=("bigquery",),
            driver_import="sqlalchemy_bigquery",
            install_extra="bigquery",
            dsn_example="bigquery://project-id/dataset_name",
            notes=(
                "Credentials via GOOGLE_APPLICATION_CREDENTIALS; read-only is enforced by "
                "granting the service account only dataViewer + jobUser."
            ),
            multi_schema=True,
            ddl_queries=_bigquery_ddl,
        ),
        DialectSpec(
            kind="trino",
            label="Trino",
            schemes=("trino",),
            driver_import="trino",
            install_extra="trino",
            dsn_example="trino://user@host:8080/catalog/schema",
            notes="Queries run through guard_sql; connect as a read-only user per catalog.",
            multi_schema=True,
            system_schemas=frozenset({"information_schema"}),
            ddl_queries=_trino_ddl,
        ),
        DialectSpec(
            kind="clickhouse",
            label="ClickHouse",
            schemes=("clickhouse", "clickhouse+native", "clickhouse+http"),
            driver_import="clickhouse_sqlalchemy",
            install_extra="clickhouse",
            dsn_example="clickhouse+native://user:pass@host:9000/dbname",
            notes="Connections send readonly=1 so the server rejects writes/DDL; pair with a read-only user.",
            multi_schema=True,
            system_schemas=frozenset({"system", "INFORMATION_SCHEMA", "information_schema"}),
            default_driver="native",
            engine_options=_clickhouse_options,
            ddl_queries=_clickhouse_ddl,
        ),
    )
}

SPEC_BY_SCHEME: dict[str, DialectSpec] = {
    scheme: spec for spec in REGISTRY.values() for scheme in spec.schemes
}


def driver_installed(spec: DialectSpec) -> bool:
    """True when the dialect's Python driver is importable (or none is needed)."""
    if spec.driver_import is None:
        return True
    try:
        return importlib.util.find_spec(spec.driver_import) is not None
    except (ImportError, ValueError):  # parent package missing / oddball __spec__
        return False


def missing_driver_message(spec: DialectSpec) -> str:
    return (
        f"The {spec.label} driver is not installed on this server. "
        f'Install it with: pip install "dqsentinel[{spec.install_extra}]"'
    )
