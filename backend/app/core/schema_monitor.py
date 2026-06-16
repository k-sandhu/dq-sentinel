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
from typing import Any

from sqlalchemy.orm import Session

from app.models import SchemaSnapshot


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


def latest_pinned_baseline(db: Session, dataset_id: int) -> SchemaSnapshot | None:
    return (
        db.query(SchemaSnapshot)
        .filter(SchemaSnapshot.dataset_id == dataset_id, SchemaSnapshot.is_baseline.is_(True))
        .order_by(SchemaSnapshot.id.desc())
        .first()
    )


def pin_baseline(db: Session, dataset_id: int, columns: list[dict[str, Any]]) -> SchemaSnapshot:
    """Pin ``columns`` as THE baseline for this dataset, clearing any previous pin."""
    db.query(SchemaSnapshot).filter(
        SchemaSnapshot.dataset_id == dataset_id, SchemaSnapshot.is_baseline.is_(True)
    ).update({SchemaSnapshot.is_baseline: False}, synchronize_session=False)
    snap = SchemaSnapshot(
        dataset_id=dataset_id,
        source="baseline",
        columns=columns,
        fingerprint=schema_fingerprint(columns),
        is_baseline=True,
    )
    db.add(snap)
    db.flush()
    return snap


def _by_name(columns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in columns}


def diff_schemas(
    baseline: list[dict[str, Any]], current: list[dict[str, Any]]
) -> dict[str, Any]:
    """Structured delta between two column schemas.

    Returns ``{added, removed, type_changed, nullability_changed, reordered}``.
    ``added``/``removed`` are column dicts; ``type_changed``/``nullability_changed``
    are ``{column, from, to}``; ``reordered`` is a bool (same name set, new order).
    """
    b, c = _by_name(baseline), _by_name(current)
    added = [c[n] for n in c if n not in b]
    removed = [b[n] for n in b if n not in c]
    type_changed: list[dict[str, Any]] = []
    nullability_changed: list[dict[str, Any]] = []
    for n in c:
        if n in b:
            if str(b[n].get("dtype")) != str(c[n].get("dtype")):
                type_changed.append({"column": n, "from": b[n].get("dtype"), "to": c[n].get("dtype")})
            if bool(b[n].get("nullable")) != bool(c[n].get("nullable")):
                nullability_changed.append(
                    {"column": n, "from": bool(b[n].get("nullable")), "to": bool(c[n].get("nullable"))}
                )
    order_b = [col["name"] for col in baseline if col["name"] in c]
    order_c = [col["name"] for col in current if col["name"] in b]
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
