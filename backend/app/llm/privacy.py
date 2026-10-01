"""Fail-closed checks for source values sent to LLMs or assistant charts."""

import sqlglot
from sqlalchemy.orm import Session
from sqlglot import exp

from app.connectors.safety import guard_sql
from app.models import Dataset, TableKnowledge

_DIALECTS = {"postgresql": "postgres", "mssql": "tsql", "clickhouse": "clickhouse"}


def pii_for_connection(db: Session, connection_id: int) -> list[str]:
    """Queries can join tables, so protect PII on every registered dataset."""
    rows = (
        db.query(TableKnowledge.pii_columns)
        .join(Dataset, Dataset.id == TableKnowledge.dataset_id)
        .filter(Dataset.connection_id == connection_id)
        .all()
    )
    return sorted({str(c) for (columns,) in rows for c in columns or []})


def guard_agent_sql(sql: str, pii_columns: list[str], kind: str) -> str:
    """Allow counts, reject projections that can expose a protected value.

    Inspect every nested SELECT, including CTEs, before execution. Wildcards and
    whole-row references cannot be proven safe without a complete source schema,
    so require explicit non-PII projections. COUNT is the only permitted aggregate
    over PII: MIN, MAX, array/string aggregates can expose actual source values.
    Output-name redaction alone cannot protect aliases or transformed values.
    """
    sql = guard_sql(sql)
    if not pii_columns:
        return sql
    pii = {c.lower() for c in pii_columns}
    try:
        tree = sqlglot.parse_one(sql, read=_DIALECTS.get(kind, kind))
    except (sqlglot.errors.SqlglotError, ValueError) as exc:
        raise ValueError(
            "Cannot verify PII safety for this SQL; use a simple SELECT with explicit columns"
        ) from exc
    if tree is None or not isinstance(tree, exp.Query):
        raise ValueError("Cannot verify PII safety for this SQL; use a SELECT")

    for table in tree.find_all(exp.Table):
        alias = table.args.get("alias")
        if not isinstance(table.this, exp.Identifier) or (alias and alias.args.get("columns")):
            raise ValueError(
                "Cannot verify PII safety for table functions or source column aliases; use named tables"
            )
    # DuckDB COLUMNS(regex) / #position can select sensitive values without ever
    # mentioning a protected column in the syntax tree.
    if any(isinstance(node, (exp.Columns, exp.PositionalColumn)) for node in tree.walk()):
        raise ValueError(
            "Dynamic or positional projections may expose PII; select explicit non-PII columns instead"
        )

    aliases = {t.alias_or_name.lower() for t in tree.find_all(exp.Table)}
    for select in tree.find_all(exp.Select):
        for projection in select.expressions:
            for node in projection.walk():
                sensitive = isinstance(node, exp.Column) and (
                    node.name.lower() in pii or (not node.table and node.name.lower() in aliases)
                )
                wildcard = isinstance(node, exp.Star)
                if not sensitive and not wildcard:
                    continue
                parent = node.parent
                # Only a direct COUNT(column) / COUNT(*) is guaranteed to return
                # a count rather than a value or an expression involving it.
                if isinstance(parent, exp.Count) and parent.this is node:
                    continue
                if isinstance(parent, exp.Distinct) and isinstance(parent.parent, exp.Count):
                    # COUNT(DISTINCT email) exposes cardinality, not addresses.
                    continue
                if sensitive:
                    raise ValueError(
                        "PII values cannot be selected, aliased, or transformed; "
                        "select non-PII columns or use COUNT(column) instead"
                    )
                raise ValueError(
                    "Wildcard projections may expose PII; select explicit non-PII columns instead"
                )
    return sql
