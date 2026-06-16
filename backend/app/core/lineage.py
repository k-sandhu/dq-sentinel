"""Table and column lineage from source DDL metadata (#51, #106).

Table lineage is built from view definitions with sqlglot table references.
Column lineage is opt-in via ``granularity=column`` and uses source metadata plus
sqlglot qualification/lineage to map view output columns back to upstream source
columns. No source data is read here; only catalog metadata and DDL are used.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlglot import exp
from sqlglot.lineage import Node as SqlglotLineageNode
from sqlglot.lineage import lineage as sqlglot_lineage
from sqlglot.optimizer.qualify import qualify

from app import models

log = logging.getLogger(__name__)

MAX_NODES = 300
MAX_COLUMNS_PER_TABLE = 120
MAX_COLUMNS_PER_VIEW = 120
MAX_COLUMN_EDGES = 5_000

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
    """Map a connection kind to the sqlglot dialect name."""
    return SQLGLOT_DIALECTS.get(kind)


def node_id_for(schema: str | None, table: str) -> str:
    """Canonical table node id."""
    return f"{schema}.{table}".lower() if schema else table.lower()


def column_id_for(table_id: str, column: str) -> str:
    return f"{table_id}.{column}".lower()


def table_key(schema: str | None, table: str) -> tuple[str | None, str]:
    """Case-insensitive (schema, table) identity."""
    return (schema.lower() if schema else None, table.lower())


def extract_table_refs(sql: str, dialect: str | None = None) -> set[tuple[str | None, str]] | None:
    """Tables referenced by ``sql`` as (schema_or_none, table_name), lowercased."""
    tree = _parse_one(sql, dialect)
    if tree is None:
        return None

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name}
    skip_targets: set[int] = set()
    if isinstance(tree, exp.Create):
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
            continue
        refs.add((schema.lower() if schema else None, table.name.lower()))
    return refs


def _parse_one(sql: str, dialect: str | None) -> exp.Expression | None:
    for read in dict.fromkeys((dialect, None)):
        try:
            return sqlglot.parse_one(sql, read=read)
        except Exception:  # noqa: BLE001 - tokenizer/parser/dialect errors -> retry
            continue
    return None


@dataclass
class DatasetHealth:
    health: str = "unknown"
    failing_checks: int = 0
    open_exceptions: int = 0


@dataclass(frozen=True)
class SourceColumn:
    schema_name: str | None
    table_name: str
    column: str


def dataset_health(db: Session, dataset_ids: list[int]) -> dict[int, DatasetHealth]:
    """Health per dataset, computed with aggregate queries."""
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


def build_schema_mapping(schema_tree: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """sqlglot schema mapping from connector.schema_tree()."""
    mapping: dict[str, dict[str, str]] = {}
    for table in schema_tree[:MAX_NODES]:
        table_name = table["table_name"]
        schema = table.get("schema_name")
        cols = {
            c["name"]: str(c.get("dtype") or "UNKNOWN")
            for c in table.get("columns") or []
            if c.get("name")
        }
        if not cols:
            continue
        mapping[table_name] = cols
        mapping[table_name.lower()] = cols
        if schema:
            mapping[f"{schema}.{table_name}"] = cols
            mapping[f"{schema}.{table_name}".lower()] = cols
    return mapping


def _query_expression(tree: exp.Expression) -> exp.Expression:
    if isinstance(tree, exp.Create) and tree.expression is not None:
        return tree.expression
    return tree


def _top_projection_expressions(query: exp.Expression) -> list[exp.Expression]:
    if isinstance(query, exp.Select):
        return list(query.expressions)
    if isinstance(query, exp.SetOperation):
        left = query.this
        if isinstance(left, exp.Select):
            return list(left.expressions)
        found = left.find(exp.Select)
        return list(found.expressions) if found else []
    found = query.find(exp.Select)
    return list(found.expressions) if found else []


def _strip_identifier(part: str) -> str:
    return part.strip().strip('"').strip("`").strip("[]")


def _column_from_lineage_name(name: str) -> str | None:
    parts = [_strip_identifier(p) for p in str(name).split(".") if _strip_identifier(p)]
    return parts[-1] if parts else None


def _source_from_leaf(node: SqlglotLineageNode) -> SourceColumn | None:
    expr_node = node.expression
    source = node.source
    table = expr_node if isinstance(expr_node, exp.Table) else source if isinstance(source, exp.Table) else None
    if table is None or not table.name:
        return None
    column = _column_from_lineage_name(node.name)
    if not column:
        return None
    return SourceColumn(table.db or None, table.name, column)


def _leaf_sources(node: SqlglotLineageNode) -> set[SourceColumn]:
    if not node.downstream:
        source = _source_from_leaf(node)
        return {source} if source is not None else set()
    out: set[SourceColumn] = set()
    for child in node.downstream:
        out |= _leaf_sources(child)
    return out


def _contains_aggregate(expr_node: exp.Expression) -> bool:
    return any(isinstance(e, exp.AggFunc) for e in expr_node.walk())


def _edge_kind(projection: exp.Expression, source_count: int, parent_query: exp.Expression) -> str:
    if isinstance(parent_query, exp.SetOperation):
        return "derived"
    inner = projection.this if isinstance(projection, exp.Alias) else projection
    if source_count == 1 and isinstance(inner, exp.Column):
        return "direct"
    if _contains_aggregate(inner):
        return "aggregate"
    return "derived"


def extract_column_lineage(
    sql: str,
    dialect: str | None,
    schema: dict[str, dict[str, str]],
    target_schema: str | None,
    target_table: str,
) -> tuple[list[dict[str, Any]], int]:
    """Return column edges for a view and an error count.

    Each edge dict has source_schema/source_table/source_column/target_column,
    kind, expression. Unresolvable output columns become an ``unresolved`` edge.
    """
    tree = _parse_one(sql, dialect)
    if tree is None:
        return [], 1
    query = _query_expression(tree)
    try:
        qualified = qualify(
            query.copy(),
            schema=schema,
            dialect=dialect,
            validate_qualify_columns=False,
            quote_identifiers=True,
        )
    except Exception:  # noqa: BLE001 - partial graph over crash
        log.warning("lineage: could not qualify column lineage for %s", target_table, exc_info=True)
        return [], 1

    projections = _top_projection_expressions(qualified)[:MAX_COLUMNS_PER_VIEW]
    edges: list[dict[str, Any]] = []
    errors = 0
    target_id = node_id_for(target_schema, target_table)
    for index, projection in enumerate(projections):
        target_column = projection.alias_or_name or f"column_{index + 1}"
        expression = projection.sql(dialect=dialect)
        try:
            root = sqlglot_lineage(target_column, qualified, schema=schema, dialect=dialect)
            sources = _leaf_sources(root)
        except Exception:  # noqa: BLE001 - one bad projection should not kill the graph
            log.warning(
                "lineage: could not resolve column %s.%s",
                target_table,
                target_column,
                exc_info=True,
            )
            sources = set()
            errors += 1

        if not sources:
            edges.append(
                {
                    "source_schema": None,
                    "source_table": "__unresolved__",
                    "source_column": target_column,
                    "target_table_id": target_id,
                    "target_column": target_column,
                    "kind": "unresolved",
                    "expression": expression,
                }
            )
            continue

        kind = _edge_kind(projection, len(sources), qualified)
        for source in sorted(sources, key=lambda s: ((s.schema_name or ""), s.table_name, s.column)):
            edges.append(
                {
                    "source_schema": source.schema_name,
                    "source_table": source.table_name,
                    "source_column": source.column,
                    "target_table_id": target_id,
                    "target_column": target_column,
                    "kind": kind,
                    "expression": expression,
                }
            )
    return edges, errors


def _column_node(
    table_id: str,
    column: str,
    dtype: str = "",
    nullable: bool = True,
    dataset_id: int | None = None,
) -> dict[str, Any]:
    return {
        "id": column_id_for(table_id, column),
        "table_id": table_id,
        "column": column,
        "dtype": dtype,
        "nullable": nullable,
        "dataset_id": dataset_id,
    }


def _add_catalog_columns(
    nodes: dict[str, dict[str, Any]],
    schema_tree: list[dict[str, Any]],
    ds_by_key: dict[tuple[str | None, str], models.Dataset],
) -> dict[tuple[str | None, str], dict[str, dict[str, Any]]]:
    columns_by_key: dict[tuple[str | None, str], dict[str, dict[str, Any]]] = {}
    for table in schema_tree:
        key = table_key(table.get("schema_name"), table["table_name"])
        table_id = node_id_for(table.get("schema_name"), table["table_name"])
        ds = ds_by_key.get(key)
        col_map: dict[str, dict[str, Any]] = {}
        for col in (table.get("columns") or [])[:MAX_COLUMNS_PER_TABLE]:
            node = _column_node(
                table_id,
                col["name"],
                str(col.get("dtype") or ""),
                bool(col.get("nullable", True)),
                ds.id if ds else None,
            )
            col_map[col["name"].lower()] = node
        columns_by_key[key] = col_map
        if table_id in nodes:
            nodes[table_id]["columns"] = list(col_map.values())
    return columns_by_key


def build_lineage(
    db: Session,
    connection: models.Connection,
    connector: Any,
    granularity: str = "table",
) -> dict[str, Any]:
    """Full lineage graph for one connection."""
    include_columns = granularity == "column"
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
        knowledge = ds.knowledge if ds else None
        nodes[node_id] = {
            "id": node_id,
            "schema_name": schema,
            "table_name": name,
            "kind": t.get("kind") or "table",
            "dataset_id": ds.id if ds else None,
            "health": h.health,
            "failing_checks": h.failing_checks,
            "open_exceptions": h.open_exceptions,
            "owner": knowledge.owner if knowledge else "",
            "importance": knowledge.importance if knowledge else "",
            "columns": [],
        }
        by_key[table_key(schema, name)] = node_id
        by_name[name.lower()].append(node_id)

    parse_errors = 0
    qualify_errors = 0
    edges: set[tuple[str, str]] = set()
    external: dict[str, dict[str, Any]] = {}
    view_ddls: dict[str, str] = {}

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
            continue
        view_ddls[view_id] = ddl
        refs = extract_table_refs(ddl, dialect)
        if refs is None:
            parse_errors += 1
            continue
        for ref_schema, ref_name in refs:
            src_id = by_key.get((ref_schema, ref_name))
            if src_id is None:
                candidates = by_name.get(ref_name, [])
                if len(candidates) == 1:
                    src_id = candidates[0]
            if src_id is None:
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
                        "owner": "",
                        "importance": "",
                        "columns": [],
                    }
            if src_id != view_id:
                edges.add((src_id, view_id))

    nodes.update(external)
    column_edges: list[dict[str, Any]] = []
    if include_columns:
        schema_tree = connector.schema_tree()
        schema_mapping = build_schema_mapping(schema_tree)
        columns_by_key = _add_catalog_columns(nodes, schema_tree, ds_by_key)
        unresolved_columns: dict[str, dict[str, Any]] = {}
        for t in tables:
            if (t.get("kind") or "table") != "view":
                continue
            view_id = node_id_for(t.get("schema_name"), t["table_name"])
            ddl = view_ddls.get(view_id)
            if not ddl:
                continue
            extracted, errors = extract_column_lineage(
                ddl,
                dialect,
                schema_mapping,
                t.get("schema_name"),
                t["table_name"],
            )
            qualify_errors += errors
            for item in extracted:
                target_node = columns_by_key.get(
                    table_key(t.get("schema_name"), t["table_name"]), {}
                ).get(str(item["target_column"]).lower())
                if target_node is None:
                    target_node = _column_node(
                        view_id,
                        item["target_column"],
                        dataset_id=nodes.get(view_id, {}).get("dataset_id"),
                    )
                    nodes.get(view_id, {}).setdefault("columns", []).append(target_node)

                if item["kind"] == "unresolved":
                    source_node = _column_node(
                        "__unresolved__",
                        f"{view_id.replace('.', '_')}__{item['source_column']}",
                        "unknown",
                        True,
                        None,
                    )
                    unresolved_columns[source_node["id"]] = source_node
                else:
                    source_id = _resolve_source_table_id(
                        item["source_schema"], item["source_table"], by_key, by_name
                    )
                    if source_id not in nodes:
                        nodes[source_id] = {
                            "id": source_id,
                            "schema_name": item["source_schema"],
                            "table_name": item["source_table"],
                            "kind": "table",
                            "dataset_id": None,
                            "health": "unknown",
                            "failing_checks": 0,
                            "open_exceptions": 0,
                            "owner": "",
                            "importance": "",
                            "columns": [],
                        }
                    source_node = (
                        columns_by_key.get(table_key(item["source_schema"], item["source_table"]), {})
                        .get(str(item["source_column"]).lower())
                    )
                    if source_node is None:
                        source_node = _column_node(source_id, item["source_column"], "unknown", True, None)
                        nodes[source_id].setdefault("columns", []).append(source_node)
                column_edges.append(
                    {
                        "source": source_node["id"],
                        "target": target_node["id"],
                        "kind": item["kind"],
                        "expression": item.get("expression"),
                    }
                )
        if unresolved_columns:
            nodes["__unresolved__"] = {
                "id": "__unresolved__",
                "schema_name": None,
                "table_name": "__unresolved__",
                "kind": "table",
                "dataset_id": None,
                "health": "unknown",
                "failing_checks": 0,
                "open_exceptions": 0,
                "owner": "",
                "importance": "",
                "columns": list(unresolved_columns.values()),
            }

    all_nodes = list(nodes.values())
    truncated = False
    if len(all_nodes) > MAX_NODES:
        all_nodes = all_nodes[:MAX_NODES]
        kept = {n["id"] for n in all_nodes}
        edges = {(s, tgt) for s, tgt in edges if s in kept and tgt in kept}
        column_edges = [
            e for e in column_edges if e["source"].rsplit(".", 1)[0] in kept and e["target"].rsplit(".", 1)[0] in kept
        ]
        truncated = True
    if len(column_edges) > MAX_COLUMN_EDGES:
        column_edges = sorted(column_edges, key=lambda e: (e["source"], e["target"]))[:MAX_COLUMN_EDGES]
        truncated = True

    return {
        "nodes": all_nodes,
        "edges": [{"source": s, "target": tgt} for s, tgt in sorted(edges)],
        "column_edges": sorted(column_edges, key=lambda e: (e["source"], e["target"], e["kind"])),
        "parse_errors": parse_errors,
        "qualify_errors": qualify_errors,
        "truncated": truncated,
    }


def _resolve_source_table_id(
    schema: str | None,
    table: str,
    by_key: dict[tuple[str | None, str], str],
    by_name: dict[str, list[str]],
) -> str:
    src_id = by_key.get(table_key(schema, table))
    if src_id is None:
        candidates = by_name.get(table.lower(), [])
        if len(candidates) == 1:
            src_id = candidates[0]
    return src_id or node_id_for(schema, table)


def subgraph(graph: dict[str, Any], node_id: str, depth: int) -> dict[str, Any]:
    """BFS table subgraph within ``depth`` hops of ``node_id``."""
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
    return _filter_graph(graph, keep)


def column_subgraph(
    graph: dict[str, Any],
    table_id: str,
    column: str,
    depth: int,
) -> dict[str, Any]:
    """BFS column subgraph for one table column, traversing both directions."""
    start = column_id_for(table_id, column)
    column_ids = {
        c["id"]
        for n in graph["nodes"]
        for c in n.get("columns") or []
    }
    if start not in column_ids:
        return {
            "nodes": [],
            "edges": [],
            "column_edges": [],
            "parse_errors": graph["parse_errors"],
            "qualify_errors": graph.get("qualify_errors", 0),
            "truncated": graph["truncated"],
        }

    adjacent: dict[str, set[str]] = defaultdict(set)
    for e in graph.get("column_edges") or []:
        adjacent[e["source"]].add(e["target"])
        adjacent[e["target"]].add(e["source"])
    keep_cols: set[str] = {start}
    frontier = {start}
    for _ in range(depth):
        frontier = {nbr for cid in frontier for nbr in adjacent[cid]} - keep_cols
        if not frontier:
            break
        keep_cols |= frontier
    keep_tables = {cid.rsplit(".", 1)[0] for cid in keep_cols}
    filtered = _filter_graph(graph, keep_tables)
    filtered["nodes"] = [
        {**n, "columns": [c for c in n.get("columns") or [] if c["id"] in keep_cols]}
        for n in filtered["nodes"]
    ]
    filtered["column_edges"] = [
        e
        for e in graph.get("column_edges") or []
        if e["source"] in keep_cols and e["target"] in keep_cols
    ]
    return filtered


def _filter_graph(graph: dict[str, Any], keep_tables: set[str]) -> dict[str, Any]:
    return {
        "nodes": [n for n in graph["nodes"] if n["id"] in keep_tables],
        "edges": [
            e for e in graph["edges"] if e["source"] in keep_tables and e["target"] in keep_tables
        ],
        "column_edges": [
            e
            for e in graph.get("column_edges") or []
            if _table_id_from_column(e["source"]) in keep_tables
            and _table_id_from_column(e["target"]) in keep_tables
        ],
        "parse_errors": graph["parse_errors"],
        "qualify_errors": graph.get("qualify_errors", 0),
        "truncated": graph["truncated"],
    }


def _table_id_from_column(column_id: str) -> str:
    parts = column_id.rsplit(".", 1)
    return parts[0] if len(parts) == 2 else column_id
