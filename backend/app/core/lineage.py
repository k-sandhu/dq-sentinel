"""Table-level lineage built by parsing view definitions with sqlglot (issue #51).

Nodes come from the connector catalog (``Connector.list_tables()``); edges come
from parsing each view's DDL (``Connector.get_ddl()``) and extracting the tables
it selects from. Data flows source -> target: the upstream table is the edge
source, the view selecting from it is the target. Check health from the app
metadata DB is overlaid per node.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlglot import exp

from app import models

log = logging.getLogger(__name__)

MAX_NODES = 300  # hard cap on graph size; sets truncated=True when exceeded

# connection.kind -> sqlglot dialect name (None -> sqlglot's generic dialect)
SQLGLOT_DIALECTS = {
    "sqlite": "sqlite",
    "duckdb": "duckdb",
    "postgresql": "postgres",
    "mysql": "mysql",
    "mssql": "tsql",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "trino": "trino",
    "clickhouse": "clickhouse",
}


def sqlglot_dialect(kind: str) -> str | None:
    """Map a connection kind to the sqlglot dialect name (None when unknown)."""
    return SQLGLOT_DIALECTS.get(kind)


def node_id_for(schema: str | None, table: str) -> str:
    """Canonical node id: "schema.table" when schema is set, else "table" (lowercased)."""
    return f"{schema}.{table}".lower() if schema else table.lower()


def table_key(schema: str | None, table: str) -> tuple[str | None, str]:
    """Case-insensitive (schema, table) identity used for matching."""
    return (schema.lower() if schema else None, table.lower())


def extract_table_refs(sql: str, dialect: str | None = None) -> set[tuple[str | None, str]] | None:
    """Tables referenced by ``sql`` as (schema_or_none, table_name) tuples, lowercased.

    CTE alias names are excluded, derived-table aliases never surface as
    ``exp.Table`` nodes, and for CREATE statements the created object itself is
    excluded. Parsing is attempted with ``dialect`` first, then sqlglot's
    generic dialect; returns None when both fail (caller counts a parse error).
    """
    tree = None
    for read in dict.fromkeys((dialect, None)):  # dialect first, then generic, deduped
        try:
            tree = sqlglot.parse_one(sql, read=read)
        except Exception:  # noqa: BLE001 - tokenizer/parser/dialect errors -> retry
            tree = None
        if tree is not None:
            break
    if tree is None:
        return None

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name}
    skip_targets: set[int] = set()
    if isinstance(tree, exp.Create):  # don't count the created view/table as its own source
        target = tree.this
        if isinstance(target, exp.Schema):
            target = target.this
        if isinstance(target, exp.Table):
            skip_targets.add(id(target))

    refs: set[tuple[str | None, str]] = set()
    for table in tree.find_all(exp.Table):
        if id(table) in skip_targets or not table.name:
            continue
        schema = table.db or None
        if schema is None and table.name.lower() in cte_names:
            continue  # reference to a CTE, not a real table
        refs.add((schema.lower() if schema else None, table.name.lower()))
    return refs


@dataclass
class DatasetHealth:
    health: str = "unknown"  # pass | warn | fail | unknown
    failing_checks: int = 0
    open_exceptions: int = 0


def dataset_health(db: Session, dataset_ids: list[int]) -> dict[int, DatasetHealth]:
    """Health per dataset, computed with three aggregate queries (not per-dataset).

    - failing_checks: active checks whose most recent CheckRun has status "fail"
    - open_exceptions: ExceptionRecords with status "open"
    - health: fail when failing_checks > 0, else warn when open_exceptions > 0,
      else pass when the dataset has at least one check run, else unknown.
    """
    if not dataset_ids:
        return {}
    latest_run = (
        db.query(models.CheckRun.check_id, func.max(models.CheckRun.id).label("last_id"))
        .filter(models.CheckRun.dataset_id.in_(dataset_ids))
        .group_by(models.CheckRun.check_id)
        .subquery()
    )
    failing = dict(
        db.query(models.CheckRun.dataset_id, func.count(models.CheckRun.id))
        .join(latest_run, models.CheckRun.id == latest_run.c.last_id)
        .join(models.Check, models.Check.id == models.CheckRun.check_id)
        .filter(models.Check.status == "active", models.CheckRun.status == "fail")
        .group_by(models.CheckRun.dataset_id)
        .all()
    )
    open_exc = dict(
        db.query(models.ExceptionRecord.dataset_id, func.count(models.ExceptionRecord.id))
        .filter(
            models.ExceptionRecord.dataset_id.in_(dataset_ids),
            models.ExceptionRecord.status == "open",
        )
        .group_by(models.ExceptionRecord.dataset_id)
        .all()
    )
    ran = {
        ds_id
        for (ds_id,) in db.query(models.CheckRun.dataset_id)
        .filter(models.CheckRun.dataset_id.in_(dataset_ids))
        .distinct()
        .all()
    }
    out: dict[int, DatasetHealth] = {}
    for ds_id in dataset_ids:
        fails = failing.get(ds_id, 0)
        opens = open_exc.get(ds_id, 0)
        if fails > 0:
            health = "fail"
        elif opens > 0:
            health = "warn"
        elif ds_id in ran:
            health = "pass"
        else:
            health = "unknown"
        out[ds_id] = DatasetHealth(health, fails, opens)
    return out


def build_lineage(db: Session, connection: models.Connection, connector: Any) -> dict[str, Any]:
    """Full lineage graph for one connection, shaped like ``schemas.LineageGraph``."""
    tables = connector.list_tables()
    dialect = sqlglot_dialect(connection.kind)

    datasets = db.query(models.Dataset).filter(models.Dataset.connection_id == connection.id).all()
    ds_by_key = {table_key(d.schema_name, d.table_name): d for d in datasets}
    health = dataset_health(db, [d.id for d in datasets])

    nodes: dict[str, dict[str, Any]] = {}
    by_key: dict[tuple[str | None, str], str] = {}
    by_name: dict[str, list[str]] = defaultdict(list)
    for t in tables:
        schema, name = t.get("schema_name"), t["table_name"]
        node_id = node_id_for(schema, name)
        if node_id in nodes:
            continue
        ds = ds_by_key.get(table_key(schema, name))
        h = health.get(ds.id, DatasetHealth()) if ds else DatasetHealth()
        nodes[node_id] = {
            "id": node_id,
            "schema_name": schema,
            "table_name": name,
            "kind": t.get("kind") or "table",
            "dataset_id": ds.id if ds else None,
            "health": h.health,
            "failing_checks": h.failing_checks,
            "open_exceptions": h.open_exceptions,
        }
        by_key[table_key(schema, name)] = node_id
        by_name[name.lower()].append(node_id)

    parse_errors = 0
    edges: set[tuple[str, str]] = set()
    external: dict[str, dict[str, Any]] = {}

    for t in tables:
        if (t.get("kind") or "table") != "view":
            continue
        view_id = node_id_for(t.get("schema_name"), t["table_name"])
        try:
            ddl, source = connector.get_ddl(t["table_name"], t.get("schema_name"))
        except Exception:  # noqa: BLE001 - keep the rest of the graph usable
            log.warning("lineage: could not fetch DDL for %s", view_id, exc_info=True)
            parse_errors += 1
            continue
        if source != "database":
            continue  # synthesized DDL carries no references — nothing to parse, not an error
        refs = extract_table_refs(ddl, dialect)
        if refs is None:
            parse_errors += 1
            continue
        for ref_schema, ref_name in refs:
            src_id = by_key.get((ref_schema, ref_name))  # exact (schema, name) match first
            if src_id is None:
                candidates = by_name.get(ref_name, [])
                if len(candidates) == 1:  # then unique table-name match
                    src_id = candidates[0]
            if src_id is None:  # unresolved -> external node (cross-database refs stay visible)
                src_id = node_id_for(ref_schema, ref_name)
                if src_id not in external:
                    external[src_id] = {
                        "id": src_id,
                        "schema_name": ref_schema,
                        "table_name": ref_name,
                        "kind": "table",
                        "dataset_id": None,
                        "health": "unknown",
                        "failing_checks": 0,
                        "open_exceptions": 0,
                    }
            if src_id != view_id:  # guard against self-references
                edges.add((src_id, view_id))

    all_nodes = list(nodes.values()) + [external[k] for k in sorted(external)]
    truncated = False
    if len(all_nodes) > MAX_NODES:
        all_nodes = all_nodes[:MAX_NODES]
        kept = {n["id"] for n in all_nodes}
        edges = {(s, tgt) for s, tgt in edges if s in kept and tgt in kept}
        truncated = True

    return {
        "nodes": all_nodes,
        "edges": [{"source": s, "target": tgt} for s, tgt in sorted(edges)],
        "parse_errors": parse_errors,
        "truncated": truncated,
    }


def subgraph(graph: dict[str, Any], node_id: str, depth: int) -> dict[str, Any]:
    """BFS subgraph within ``depth`` hops of ``node_id``, traversing edges in
    both directions. ``parse_errors``/``truncated`` carry over from the full graph."""
    known = {n["id"] for n in graph["nodes"]}
    keep: set[str] = {node_id} & known
    adjacent: dict[str, set[str]] = defaultdict(set)
    for e in graph["edges"]:
        adjacent[e["source"]].add(e["target"])
        adjacent[e["target"]].add(e["source"])
    frontier = set(keep)
    for _ in range(depth):
        frontier = {nbr for nid in frontier for nbr in adjacent[nid]} - keep
        if not frontier:
            break
        keep |= frontier
    return {
        "nodes": [n for n in graph["nodes"] if n["id"] in keep],
        "edges": [e for e in graph["edges"] if e["source"] in keep and e["target"] in keep],
        "parse_errors": graph["parse_errors"],
        "truncated": graph["truncated"],
    }
