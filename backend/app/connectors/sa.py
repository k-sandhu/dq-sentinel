"""SQLAlchemy-based source connectors.

Supported engines are declared in app/connectors/dialects.py (sqlite, duckdb,
postgresql, mysql/mariadb, mssql, snowflake, bigquery, trino, clickhouse).
Engines are opened read-only where the driver supports it and cached per
connection id. All ad-hoc SQL goes through guard_sql() + enforce_limit().
Drivers beyond the bundled sqlite/duckdb are optional extras: engine creation
raises DriverNotInstalled with install instructions when one is missing.
"""

import threading
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import NoSuchModuleError

from app.connectors.dialects import (
    REGISTRY,
    SPEC_BY_SCHEME,
    DialectSpec,
    DriverNotInstalled,
    missing_driver_message,
)
from app.connectors.safety import SqlNotAllowed, enforce_limit, guard_sql
from app.models import Connection
from app.observability import SOURCE_QUERIES, SOURCE_QUERY_SECONDS

ALLOWED_SCHEMES = frozenset(SPEC_BY_SCHEME)

_engines: dict[int, Engine] = {}
_lock = threading.Lock()

# ---------------------------------------------------------------- sampling (#269)
# Seed used for every engine-native sample. It is a module constant, NOT a per-call
# value, on purpose: a profile baseline and a distribution_drift check's current
# window are drawn by two different code paths, and on engines with a seedable
# sampler the same seed makes them draw the same rows out of unchanged data. Two
# independently-seeded samples would still be unbiased, but identical ones make PSI
# on unchanged data exactly 0 instead of "small".
SAMPLE_SEED = 1337

# Ceiling for the ORDER BY RANDOM() fallback. That fallback is a top-N sort of the
# whole table, so it must never be what a 100M-row table gets by default; past this
# many rows an engine with no native sampler falls back to the positional read and
# says so (`representative=False`) rather than melting the source.
RANDOM_SORT_MAX_ROWS = 2_000_000

# How each engine spells "a fresh random value per row" in ORDER BY. SQL Server's
# RAND() is evaluated ONCE per statement (it would order by a constant), so it gets
# NEWID(); everything else has a genuine per-row function.
_RANDOM_ORDER_FN: dict[str, str] = {
    "sqlite": "RANDOM()",
    "duckdb": "RANDOM()",
    "postgresql": "RANDOM()",
    "mysql": "RAND()",
    "mssql": "NEWID()",
    "snowflake": "RANDOM()",
    "bigquery": "RAND()",
    "trino": "RANDOM()",
    "clickhouse": "rand()",
}


@dataclass(frozen=True)
class Sample:
    """A bounded read plus an honest description of HOW it was drawn.

    ``method`` is one of:

    ``full``
        The table fits inside the cap — every row was read, nothing was sampled.
    ``reservoir``
        DuckDB ``USING SAMPLE reservoir(n ROWS) REPEATABLE (seed)`` — a uniform
        random sample of exactly n rows in one scan, reproducible.
    ``bernoulli``
        PostgreSQL ``TABLESAMPLE BERNOULLI (pct) REPEATABLE (seed)`` — per-row coin
        flip, reproducible. (SYSTEM is deliberately not used: it samples whole pages,
        which on a clustered table reproduces the very bias this exists to remove.)
    ``random_sort``
        ``ORDER BY <random> LIMIT n`` — representative but NOT reproducible, and a
        top-N sort, so it is gated on ``RANDOM_SORT_MAX_ROWS``.
    ``head``
        The old behaviour: a bare ``LIMIT n`` with no ORDER BY, i.e. whatever rows
        the engine reaches first. Deterministic but positionally biased — on a table
        written in time order this is "the oldest n rows", not a sample (#269).
        ``representative`` is False here and callers must label the stats they
        derive from it.
    """

    df: pd.DataFrame
    method: str
    representative: bool
    reproducible: bool
    truncated: bool  # the table is larger than the cap, so this is a real sample
    seed: int | None
    row_count: int | None

    def as_facts(self, requested_rows: int) -> dict[str, Any]:
        """JSON-able description for a profile payload / check metrics."""
        return {
            "method": self.method,
            "representative": self.representative,
            "reproducible": self.reproducible,
            "sampled": self.truncated,
            "seed": self.seed,
            "rows": int(len(self.df)),
            "requested_rows": int(requested_rows),
            "row_count": self.row_count,
        }


def _spec_from_dsn(dsn: str) -> DialectSpec:
    scheme = make_url(dsn).drivername
    spec = SPEC_BY_SCHEME.get(scheme)
    if spec is None:
        raise SqlNotAllowed(
            f"Unsupported DSN scheme '{scheme}'. Supported kinds: {', '.join(sorted(REGISTRY))}"
        )
    return spec


def kind_from_dsn(dsn: str) -> str:
    return _spec_from_dsn(dsn).kind


def _create_engine(spec: DialectSpec, url: str | URL, kwargs: dict[str, Any]) -> Engine:
    """create_engine() with missing optional drivers translated to DriverNotInstalled."""
    try:
        return create_engine(url, **kwargs)
    except (ImportError, NoSuchModuleError) as exc:  # ModuleNotFoundError subclasses ImportError
        if spec.install_extra is None:  # bundled driver — a missing module is a real bug
            raise
        raise DriverNotInstalled(missing_driver_message(spec)) from exc


def _readonly_engine(dsn: str) -> Engine:
    url = make_url(dsn)
    spec = _spec_from_dsn(dsn)
    if spec.kind == "duckdb" and url.query:
        # duckdb-engine merges the DSN's query string into DuckDB's own config
        # (create_connect_args -> url_config -> duckdb.connect(config=...)), and it
        # merges it AFTER our connect_args. A connection authored with
        # `?enable_external_access=true` would therefore override the engine-level
        # kill switch in dialects.py and reopen host-file reads (#267). Nothing in
        # the query string is needed to open a .duckdb file, so the whole config
        # surface is dropped rather than denylisting one key that can be renamed.
        url = url.difference_update_query(list(url.query))
    kwargs = spec.engine_options(url)
    if spec.kind == "sqlite":
        # Reopen via URI with mode=ro so writes fail at the driver level.
        db_path = (url.database or "").replace("\\", "/")
        if db_path and db_path != ":memory:":
            uri = f"file:{db_path}?mode=ro&uri=true"
            return _create_engine(spec, "sqlite:///" + uri, kwargs)
        return _create_engine(spec, dsn, kwargs)
    if spec.default_driver and "+" not in url.drivername:
        url = url.set(drivername=f"{url.drivername}+{spec.default_driver}")
    return _create_engine(spec, url, kwargs)


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
        self.spec = _spec_from_dsn(dsn)
        self.kind = self.spec.kind
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
        if self.spec.multi_schema:
            excluded = {s.lower() for s in self.spec.system_schemas}
            schemas = [s for s in insp.get_schema_names() if s.lower() not in excluded]
        else:
            schemas = [None]
        out: list[dict[str, Any]] = []
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

    def schema_tree(self) -> list[dict[str, Any]]:
        """All tables/views with their columns — for the workbench sidebar."""
        out = []
        for t in self.list_tables():
            try:
                cols = self.get_columns(t["table_name"], t["schema_name"])
            except Exception:  # noqa: BLE001 - skip objects we can't introspect
                cols = []
            out.append({**t, "columns": cols})
        return out

    def get_ddl(self, table: str, schema: str | None = None) -> tuple[str, str]:
        """Return (ddl, source). Real definition where the dialect exposes it,
        otherwise a CREATE TABLE synthesized from introspection."""
        try:
            if self.kind == "sqlite":
                ddl = self.scalar(
                    "SELECT sql FROM sqlite_master WHERE name = :t", {"t": table}
                )
                if ddl:
                    return str(ddl), "database"
            elif self.kind == "duckdb":
                ddl = self.scalar(
                    "SELECT sql FROM duckdb_views() WHERE view_name = :t", {"t": table}
                )
                if ddl:
                    return str(ddl), "database"
                ddl = self.scalar(
                    "SELECT sql FROM duckdb_tables() WHERE table_name = :t", {"t": table}
                )
                if ddl:
                    return str(ddl), "database"
            elif self.kind == "postgresql":
                ddl = self.scalar(
                    "SELECT pg_get_viewdef(c.oid, true) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relname = :t AND c.relkind IN ('v', 'm') "
                    "AND n.nspname = COALESCE(:s, n.nspname)",
                    {"t": table, "s": schema},
                )
                if ddl:
                    return f"CREATE VIEW {table} AS\n{ddl}", "database"
            elif self.spec.ddl_queries is not None:
                # Registry-driven catalog lookups: each attempt is a single
                # SELECT that goes through guard_sql() inside self.scalar().
                for sql, params in self.spec.ddl_queries(table, schema, self.quote):
                    ddl = self.scalar(sql, params)
                    if ddl:
                        return str(ddl), "database"
        except Exception:  # noqa: BLE001 - fall through to synthesized DDL
            pass

        cols = self.get_columns(table, schema)
        body = ",\n".join(
            f"    {c['name']} {c['dtype']}{'' if c['nullable'] else ' NOT NULL'}" for c in cols
        )
        return f"CREATE TABLE {self.table_ref(table, schema)} (\n{body}\n);", "synthesized"

    # ---- querying ----
    def _observe(self, start: float) -> None:
        SOURCE_QUERIES.labels(self.kind).inc()
        SOURCE_QUERY_SECONDS.labels(self.kind).observe(time.perf_counter() - start)

    def run_select(
        self, sql: str, params: dict[str, Any] | None = None, limit: int | None = None
    ) -> QueryResult:
        """Guarded ad-hoc SELECT. Used by check compilers, previews, and LLM agents."""
        cleaned = guard_sql(sql)
        if limit is not None:
            cleaned = enforce_limit(cleaned, limit)
        start = time.perf_counter()
        try:
            with self.engine.connect() as conn:
                res = conn.execute(text(cleaned), params or {})
                cols = list(res.keys())
                rows = [list(r) for r in res.fetchall()]
        finally:
            self._observe(start)
        return QueryResult(columns=cols, rows=rows)

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        cleaned = guard_sql(sql)
        start = time.perf_counter()
        try:
            with self.engine.connect() as conn:
                return conn.execute(text(cleaned), params or {}).scalar()
        finally:
            self._observe(start)

    def row_count(self, table: str, schema: str | None = None) -> int:
        return int(self.scalar(f"SELECT COUNT(*) FROM {self.table_ref(table, schema)}") or 0)

    def fetch_df(self, sql: str, limit: int) -> pd.DataFrame:
        """Bounded DataFrame fetch for profiling / ML sampling.

        NOTE: this is a bare ``LIMIT`` — the FIRST n rows the engine reaches, not a
        sample. Anything computing a *statistic* wants ``sample_df()`` instead.
        """
        cleaned = enforce_limit(guard_sql(sql), limit)
        start = time.perf_counter()
        try:
            with self.engine.connect() as conn:
                return pd.read_sql(text(cleaned), conn)
        finally:
            self._observe(start)

    def _sample_candidates(
        self, select: str, ref: str, limit: int, row_count: int | None, seed: int
    ) -> list[tuple[str, str, bool]]:
        """Ordered (method, sql, reproducible) attempts, best first."""
        out: list[tuple[str, str, bool]] = []
        if self.kind == "duckdb":
            # Reservoir sampling: one pass, exactly `limit` rows, uniform over the
            # whole table regardless of physical clustering.
            out.append(
                (
                    "reservoir",
                    f"SELECT {select} FROM {ref} USING SAMPLE reservoir({limit} ROWS) "
                    f"REPEATABLE ({seed})",
                    True,
                )
            )
        elif self.kind == "postgresql" and row_count:
            # TABLESAMPLE takes a percentage, so it needs the row count. Aim at exactly
            # `limit` rows: overshoot would be trimmed by the outer LIMIT, and trimming
            # is positional again. Undershoot just means a slightly smaller sample.
            pct = min(100.0, max(round(limit / row_count * 100.0, 6), 0.000001))
            out.append(
                (
                    "bernoulli",
                    f"SELECT {select} FROM {ref} TABLESAMPLE BERNOULLI ({pct}) "
                    f"REPEATABLE ({seed})",
                    True,
                )
            )
        random_fn = _RANDOM_ORDER_FN.get(self.kind)
        if random_fn and row_count is not None and row_count <= RANDOM_SORT_MAX_ROWS:
            # Inner LIMIT makes this a top-N sort rather than a full materialised sort,
            # and it also pins the ordering that the enforce_limit() wrapper would
            # otherwise be free to discard.
            out.append(
                ("random_sort", f"SELECT {select} FROM {ref} ORDER BY {random_fn} LIMIT {limit}", False)
            )
        return out

    def sample_df(
        self,
        select: str,
        ref: str,
        limit: int,
        *,
        row_count: int | None = None,
        seed: int = SAMPLE_SEED,
        reproducible_only: bool = False,
    ) -> Sample:
        """Representative bounded read of ``SELECT {select} FROM {ref}`` (#269).

        ``fetch_df`` returns the first n rows the engine reaches. On a large table
        written in time order those rows are one contiguous, temporally clustered
        slice, so every statistic derived from them (mean, stddev, p1..p99, pattern
        ratios) describes the oldest slice of the table while being served next to an
        exact full-table row count. This draws a sample instead: engine-native where
        the engine has a sampler, an ORDER BY RANDOM() top-N where it does not and the
        table is small enough to afford the sort, and — only when neither is possible
        — the old positional read, flagged ``representative=False`` so the caller can
        label the stats honestly rather than pretending.

        Every candidate still goes through ``guard_sql`` + ``enforce_limit`` (via
        ``fetch_df``), so the read-only guarantees are unchanged. ``select``/``ref``
        must already be quoted by the caller; ``limit``/``seed`` are coerced to int.

        ``reproducible_only`` drops the un-seeded ``random_sort`` candidate. Callers
        that compare two reads of the *same* table to each other (KS drift measures
        run-over-run) need both draws to land on the same rows when nothing changed;
        two independent random draws would make such a comparison fire at its own
        significance level on data nobody touched.
        """
        limit = int(limit)
        seed = int(seed)
        base = f"SELECT {select} FROM {ref}"
        if row_count is not None and row_count <= limit:
            # Not a sample at all — the cap already covers the table.
            return Sample(
                df=self.fetch_df(base, limit), method="full", representative=True,
                reproducible=True, truncated=False, seed=None, row_count=row_count,
            )
        for method, sql, reproducible in self._sample_candidates(select, ref, limit, row_count, seed):
            if reproducible_only and not reproducible:
                continue
            try:
                df = self.fetch_df(sql, limit)
            except Exception:  # noqa: BLE001 - sampling is best-effort; e.g. TABLESAMPLE
                continue       # is rejected on a view, and a sample must never fail a run
            if len(df) == 0 and row_count:
                continue  # a rounded-down percentage that selected nothing
            return Sample(
                df=df, method=method, representative=True, reproducible=reproducible,
                truncated=True, seed=seed if reproducible else None, row_count=row_count,
            )
        df = self.fetch_df(base, limit)
        return Sample(
            df=df, method="head", representative=False, reproducible=True,
            truncated=row_count > limit if row_count is not None else len(df) >= limit,
            seed=None, row_count=row_count,
        )

    def test(self) -> tuple[bool, str, int | None]:
        try:
            tables = self.list_tables()
            return True, f"Connected ({self.kind}); {len(tables)} tables/views visible", len(tables)
        except Exception as exc:  # noqa: BLE001 - surface driver errors to the user
            return False, f"Connection failed: {exc}", None


def connector_for(connection: Connection) -> Connector:
    return Connector(connection.dsn, connection_id=connection.id)
