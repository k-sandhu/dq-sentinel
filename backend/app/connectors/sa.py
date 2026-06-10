"""SQLAlchemy-based source connectors (SQLite, DuckDB, PostgreSQL).

Engines are opened read-only where the driver supports it and cached per
connection id. All ad-hoc SQL goes through guard_sql() + enforce_limit().
"""

import threading
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

from app.connectors.safety import SqlNotAllowed, enforce_limit, guard_sql
from app.models import Connection

ALLOWED_SCHEMES = {"sqlite", "duckdb", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}

_engines: dict[int, Engine] = {}
_lock = threading.Lock()


def kind_from_dsn(dsn: str) -> str:
    scheme = make_url(dsn).drivername
    if scheme not in ALLOWED_SCHEMES:
        raise SqlNotAllowed(
            f"Unsupported DSN scheme '{scheme}'. Allowed: sqlite, duckdb, postgresql"
        )
    return scheme.split("+")[0]


def _readonly_engine(dsn: str) -> Engine:
    url = make_url(dsn)
    kind = kind_from_dsn(dsn)
    if kind == "sqlite":
        # Reopen via URI with mode=ro so writes fail at the driver level.
        db_path = (url.database or "").replace("\\", "/")
        if db_path and db_path != ":memory:":
            uri = f"file:{db_path}?mode=ro&uri=true"
            return create_engine("sqlite:///" + uri, connect_args={"check_same_thread": False})
        return create_engine(dsn, connect_args={"check_same_thread": False})
    if kind == "duckdb":
        return create_engine(dsn, connect_args={"read_only": True})
    # postgresql: force read-only transactions for the session
    return create_engine(
        dsn,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )


def dispose_connection(connection_id: int) -> None:
    with _lock:
        eng = _engines.pop(connection_id, None)
    if eng is not None:
        eng.dispose()


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]


class Connector:
    def __init__(self, dsn: str, connection_id: int | None = None):
        self.dsn = dsn
        self.kind = kind_from_dsn(dsn)
        if connection_id is not None:
            with _lock:
                eng = _engines.get(connection_id)
                if eng is None:
                    eng = _readonly_engine(dsn)
                    _engines[connection_id] = eng
            self.engine = eng
        else:
            self.engine = _readonly_engine(dsn)

    # ---- identifiers ----
    def quote(self, ident: str) -> str:
        return self.engine.dialect.identifier_preparer.quote(ident)

    def table_ref(self, table: str, schema: str | None = None) -> str:
        if schema:
            return f"{self.quote(schema)}.{self.quote(table)}"
        return self.quote(table)

    # ---- introspection ----
    def list_tables(self) -> list[dict[str, Any]]:
        insp = inspect(self.engine)
        out: list[dict[str, Any]] = []
        if self.kind == "postgresql":
            schemas = [s for s in insp.get_schema_names() if s not in ("information_schema", "pg_catalog")]
        else:
            schemas = [None]
        for schema in schemas:
            for name in insp.get_table_names(schema=schema):
                out.append({"schema_name": schema, "table_name": name, "kind": "table"})
            for name in insp.get_view_names(schema=schema):
                out.append({"schema_name": schema, "table_name": name, "kind": "view"})
        return sorted(out, key=lambda t: ((t["schema_name"] or ""), t["table_name"]))

    def get_columns(self, table: str, schema: str | None = None) -> list[dict[str, Any]]:
        insp = inspect(self.engine)
        return [
            {"name": c["name"], "dtype": str(c["type"]), "nullable": bool(c.get("nullable", True))}
            for c in insp.get_columns(table, schema=schema)
        ]

    # ---- querying ----
    def run_select(
        self, sql: str, params: dict[str, Any] | None = None, limit: int | None = None
    ) -> QueryResult:
        """Guarded ad-hoc SELECT. Used by check compilers, previews, and LLM agents."""
        cleaned = guard_sql(sql)
        if limit is not None:
            cleaned = enforce_limit(cleaned, limit)
        with self.engine.connect() as conn:
            res = conn.execute(text(cleaned), params or {})
            cols = list(res.keys())
            rows = [list(r) for r in res.fetchall()]
        return QueryResult(columns=cols, rows=rows)

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        cleaned = guard_sql(sql)
        with self.engine.connect() as conn:
            return conn.execute(text(cleaned), params or {}).scalar()

    def row_count(self, table: str, schema: str | None = None) -> int:
        return int(self.scalar(f"SELECT COUNT(*) FROM {self.table_ref(table, schema)}") or 0)

    def fetch_df(self, sql: str, limit: int) -> pd.DataFrame:
        """Bounded DataFrame fetch for profiling / ML sampling."""
        cleaned = enforce_limit(guard_sql(sql), limit)
        with self.engine.connect() as conn:
            return pd.read_sql(text(cleaned), conn)

    def test(self) -> tuple[bool, str, int | None]:
        try:
            tables = self.list_tables()
            return True, f"Connected ({self.kind}); {len(tables)} tables/views visible", len(tables)
        except Exception as exc:  # noqa: BLE001 - surface driver errors to the user
            return False, f"Connection failed: {exc}", None


def connector_for(connection: Connection) -> Connector:
    return Connector(connection.dsn, connection_id=connection.id)
