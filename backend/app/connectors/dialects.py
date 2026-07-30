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

# (quoted column, its declared type as Connector.get_columns() renders it) -> a SQL
# boolean that is TRUE only for an IEEE NaN, or None when a column of that type
# cannot hold one. Used by the `range` check, which otherwise lets NaN through
# whenever no upper bound is configured (#271).
NanPredicateBuilder = Callable[[str, str], str | None]


class DriverNotInstalled(RuntimeError):
    """A supported dialect whose optional driver package is missing on this server."""


def _no_options(url: URL) -> dict[str, Any]:
    return {}


def _sqlite_options(url: URL) -> dict[str, Any]:
    # The mode=ro URI rewrite itself happens in sa.py (it changes the DSN, not
    # the kwargs); writes fail at the driver level once reopened read-only.
    return {"connect_args": {"check_same_thread": False}}


def _duckdb_options(url: URL) -> dict[str, Any]:
    # read_only=True stops writes. It does NOT stop *reads* of the host filesystem:
    # DuckDB's replacement scan turns a bare string literal in table position into a
    # file read (`SELECT * FROM '/etc/passwd'`), with no function call for guard_sql's
    # denylist to match (#267/#281). enable_external_access is DuckDB's own kill
    # switch — it disables every file/network access originating from SQL
    # (replacement scans, glob(), read_csv/read_parquet, httpfs, ATTACH) regardless
    # of any gap in the regex guard. Opening the .duckdb database file named by the
    # DSN is unaffected: that file is the database, not an "external" resource.
    # It does cost a capability: a catalog object *defined over* an external file
    # (`CREATE VIEW v AS SELECT * FROM read_parquet(...)`) is refused as well — see
    # the notes below. That is deliberate; a per-connection opt-out would reopen #267.
    # sa.py strips the DSN query string first, because duckdb-engine merges it into
    # this same config and would otherwise let the DSN turn the switch back on.
    return {"connect_args": {"read_only": True, "config": {"enable_external_access": "false"}}}


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


# ---------------------------------------------------------------- NaN predicates
# `WHERE col IS NOT NULL AND col < :min` silently passes a NaN: NaN is not NULL and
# no comparison against a bound is true for it, so a min-only range check gave a
# clean bill of health on genuinely invalid data (#271). Each engine therefore
# declares how to *name* a NaN, because the obvious IEEE test is not portable:
#
#   * `col != col` (NaN != NaN) is WRONG here. Verified live on both engines this
#     repo bundles: DuckDB and PostgreSQL both define `NaN = NaN` as TRUE and sort
#     NaN above every other value, so `!=` matches nothing and `col > :max` is what
#     was catching NaN on two-sided ranges all along.
#   * PostgreSQL has no isnan(); the portable test there is `col = 'NaN'`, with the
#     untyped literal resolved to the column's own type (works for float8 *and*
#     numeric, both of which accept NaN).
#   * The predicate must be gated on the column type, which is why the builder gets
#     the dtype: `isnan(<date>)` is a DuckDB binder error and `<int col> = 'NaN'` is
#     a PostgreSQL conversion error, so an ungated predicate would break every
#     date/integer range check — a worse regression than the bug.
#
# Engines with no builder cannot hold a NaN at all, so the fix is a no-op there and
# costs them not even the column-type lookup:
#   * SQLite stores NaN as NULL (verified) — a not_null check catches it instead.
#   * MySQL/MariaDB and SQL Server reject NaN in numeric columns outright.
#
# VERIFICATION STATUS, since only some of this can be pinned by CI:
#   * duckdb — behaviour proven end-to-end in tests/test_checks.py against a real
#     .duckdb file (duckdb is a bundled dependency, so this runs everywhere).
#   * postgresql — semantics and end-to-end behaviour verified by hand against
#     postgres:16 (float8 and numeric NaN caught on min-only / max-only / two-sided;
#     integer, date and text columns left untouched). CI has no PostgreSQL *source*
#     server, so the suite can only pin the emitted predicate, not run it.
#   * snowflake / bigquery / trino / clickhouse — vendor-documented spelling, never
#     executed here. Gated to binary-float columns so a wrong spelling surfaces as a
#     loud error on a float range check, never as a silent wrong answer.

# Type names as Connector.get_columns() renders them (SQLAlchemy type strings):
# "DOUBLE PRECISION", "FLOAT", "REAL", "FLOAT64", "Float64", "NUMERIC(10, 2)".
_BINARY_FLOAT_TYPES = ("DOUBLE", "FLOAT", "REAL")
# Exact numerics hold NaN on PostgreSQL only; DuckDB DECIMAL, Snowflake NUMBER and
# BigQuery NUMERIC cannot, and Snowflake even errors on `NUMBER = 'NaN'`.
_EXACT_NUMERIC_TYPES = ("NUMERIC", "DECIMAL")


def _type_matches(dtype: str, tokens: tuple[str, ...]) -> bool:
    upper = dtype.upper()
    return any(token in upper for token in tokens)


def _nan_function(fn: str) -> NanPredicateBuilder:
    """NaN test via the engine's own isnan() function, for float columns only."""

    def build(col: str, dtype: str) -> str | None:
        return f"{fn}({col})" if _type_matches(dtype, _BINARY_FLOAT_TYPES) else None

    return build


def _nan_equality(*, exact_numeric: bool) -> NanPredicateBuilder:
    """NaN test by comparison with the 'NaN' literal, for engines where NaN equals
    itself and there is no isnan(). The literal is a constant, never user input."""
    types = _BINARY_FLOAT_TYPES + (_EXACT_NUMERIC_TYPES if exact_numeric else ())

    def build(col: str, dtype: str) -> str | None:
        return f"{col} = 'NaN'" if _type_matches(dtype, types) else None

    return build


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
    nan_predicate: NanPredicateBuilder | None = None  # None = this engine cannot store NaN


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
            notes=(
                "Opened with read_only=True and enable_external_access=false, so DuckDB "
                "rejects writes and any file/network read from SQL at the driver level; "
                "DSN query parameters are dropped so the setting cannot be overridden. "
                "Trade-off: objects whose definition reads an EXTERNAL file — a view over "
                "read_parquet/read_csv, a lake table — are refused too, so point this at a "
                "database file that holds its own data."
            ),
            engine_options=_duckdb_options,
            nan_predicate=_nan_function("isnan"),  # verified live
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
            # No isnan() in PostgreSQL; NaN equals itself, and numeric holds it too.
            nan_predicate=_nan_equality(exact_numeric=True),  # verified live
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
            # Snowflake has no isnan(); 'NaN' casts into FLOAT and equals itself.
            # NUMBER (its DECIMAL) cannot hold NaN and errors on the cast, hence
            # exact_numeric=False. Vendor-documented, not exercised live here.
            nan_predicate=_nan_equality(exact_numeric=False),
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
            nan_predicate=_nan_function("IS_NAN"),  # vendor-documented, not live-tested
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
            nan_predicate=_nan_function("is_nan"),  # vendor-documented, not live-tested
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
            nan_predicate=_nan_function("isNaN"),  # vendor-documented, not live-tested
        ),
    )
}

SPEC_BY_SCHEME: dict[str, DialectSpec] = {
    scheme: spec for spec in REGISTRY.values() for scheme in spec.schemes
}


def nan_predicate_builder(kind: str) -> NanPredicateBuilder | None:
    """How this engine names a NaN, or None when it cannot store one.

    Callers check for None *before* looking up a column's type, so engines that
    cannot hold a NaN pay nothing for the check.
    """
    spec = REGISTRY.get(kind)
    return spec.nan_predicate if spec is not None else None


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
