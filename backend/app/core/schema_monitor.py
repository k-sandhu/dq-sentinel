"""Schema-change monitoring (issue #101).

Introspect a dataset's column schema, persist deduped snapshots, pin a baseline,
and diff two schemas. Shared by the ``schema_change`` check (core/check_types),
the profiler hook (api/datasets), and the schema-history/baseline endpoints.

Detection itself is run-over-run (the check compares against the previous run's
stored schema, or against the pinned baseline); snapshots here are the history
timeline and the pinned-baseline store.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import SchemaSnapshot

log = logging.getLogger(__name__)


def introspect_columns(connector: Any, table: str, schema: str | None = None) -> list[dict[str, Any]]:
    """Normalized column schema: ``[{name, dtype, nullable, ordinal}]`` in column order."""
    return [
        {"name": c["name"], "dtype": str(c["dtype"]), "nullable": bool(c["nullable"]), "ordinal": i}
        for i, c in enumerate(connector.get_columns(table, schema))
    ]


def schema_fingerprint(columns: list[dict[str, Any]]) -> str:
    """Stable SHA-256 over (ordinal, name, dtype, nullable) — any change moves it."""
    norm = [[c["ordinal"], c["name"], c["dtype"], c["nullable"]] for c in columns]
    return hashlib.sha256(json.dumps(norm, sort_keys=True, default=str).encode()).hexdigest()


def latest_snapshot(db: Session, dataset_id: int) -> SchemaSnapshot | None:
    return (
        db.query(SchemaSnapshot)
        .filter(SchemaSnapshot.dataset_id == dataset_id)
        .order_by(SchemaSnapshot.id.desc())
        .first()
    )


def capture_schema_snapshot(
    db: Session, dataset_id: int, columns: list[dict[str, Any]], source: str = "profile"
) -> SchemaSnapshot | None:
    """Insert a snapshot iff the schema differs from the latest one (dedupe)."""
    fp = schema_fingerprint(columns)
    latest = latest_snapshot(db, dataset_id)
    if latest is not None and latest.fingerprint == fp:
        return None
    snap = SchemaSnapshot(dataset_id=dataset_id, source=source, columns=columns, fingerprint=fp)
    db.add(snap)
    db.flush()
    return snap


def latest_pinned_baseline(db: Session, dataset_id: int, scope: str = "manual") -> SchemaSnapshot | None:
    return (
        db.query(SchemaSnapshot)
        .filter(SchemaSnapshot.dataset_id == dataset_id, SchemaSnapshot.is_baseline.is_(True),
                SchemaSnapshot.baseline_scope == scope)
        .order_by(SchemaSnapshot.id.desc())
        .first()
    )


def pin_baseline(
    db: Session, dataset_id: int, columns: list[dict[str, Any]], scope: str = "manual"
) -> SchemaSnapshot:
    """Replace only this consumer's pin, preserving other consumers' baselines."""
    db.query(SchemaSnapshot).filter(
        SchemaSnapshot.dataset_id == dataset_id, SchemaSnapshot.is_baseline.is_(True),
        SchemaSnapshot.baseline_scope == scope,
    ).update({SchemaSnapshot.is_baseline: False}, synchronize_session=False)
    snap = SchemaSnapshot(
        dataset_id=dataset_id,
        source="baseline",
        columns=columns,
        fingerprint=schema_fingerprint(columns),
        is_baseline=True,
        baseline_scope=scope,
    )
    db.add(snap)
    db.flush()
    return snap


def case_collisions(*schemas: list[dict[str, Any]]) -> set[str]:
    """Identify case-sensitive duplicates without silently merging real columns.

    Connectors fold identifiers differently (Postgres lower, Snowflake upper).
    Match case-insensitively except names that collide within either schema.
    """
    collisions: set[str] = set()
    for columns in schemas:
        seen: set[str] = set()
        for col in columns:
            key = col["name"].lower()
            if key in seen:
                collisions.add(key)
            seen.add(key)
    if collisions:
        log.warning("Column names collide after case folding; comparing those names exactly",
                    extra={"event": "schema.case_collision"})
    return collisions


def column_key(name: str, collisions: set[str]) -> str:
    return name if name.lower() in collisions else name.lower()


def columns_by_name(columns: list[dict[str, Any]], collisions: set[str]) -> dict[str, dict[str, Any]]:
    return {column_key(c["name"], collisions): c for c in columns}


def diff_schemas(
    baseline: list[dict[str, Any]], current: list[dict[str, Any]]
) -> dict[str, Any]:
    """Structured delta between two column schemas.

    Returns ``{added, removed, type_changed, nullability_changed, reordered}``.
    ``added``/``removed`` are column dicts; ``type_changed``/``nullability_changed``
    are ``{column, from, to}``; ``reordered`` is a bool (same name set, new order).
    """
    collisions = case_collisions(baseline, current)
    b, c = columns_by_name(baseline, collisions), columns_by_name(current, collisions)
    added = [c[n] for n in c if n not in b]
    removed = [b[n] for n in b if n not in c]
    type_changed: list[dict[str, Any]] = []
    nullability_changed: list[dict[str, Any]] = []
    for n in c:
        if n in b:
            if str(b[n].get("dtype")) != str(c[n].get("dtype")):
                type_changed.append({"column": c[n]["name"], "from": b[n].get("dtype"), "to": c[n].get("dtype")})
            if bool(b[n].get("nullable")) != bool(c[n].get("nullable")):
                nullability_changed.append(
                    {"column": c[n]["name"], "from": bool(b[n].get("nullable")), "to": bool(c[n].get("nullable"))}
                )
    order_b = [column_key(col["name"], collisions) for col in baseline if column_key(col["name"], collisions) in c]
    order_c = [column_key(col["name"], collisions) for col in current if column_key(col["name"], collisions) in b]
    reordered = set(b) == set(c) and order_b != order_c
    return {
        "added": added,
        "removed": removed,
        "type_changed": type_changed,
        "nullability_changed": nullability_changed,
        "reordered": reordered,
    }


def summarize_delta(delta: dict[str, Any]) -> dict[str, Any]:
    """Compact counts for the history timeline UI."""
    return {
        "added": [c["name"] for c in delta["added"]],
        "removed": [c["name"] for c in delta["removed"]],
        "type_changed": len(delta["type_changed"]),
        "nullability_changed": len(delta["nullability_changed"]),
        "reordered": bool(delta["reordered"]),
    }
