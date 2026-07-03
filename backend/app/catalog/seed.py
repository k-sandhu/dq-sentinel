"""Connect / disconnect / status engine for the built-in data catalog.

``connect_entry`` materializes a *fully-governed* enterprise dataset in a single
transaction, reusing the platform's own services so the result is
indistinguishable from a hand-built one:

    generate backing data -> Connection -> Datasets -> profile -> knowledge ->
    freshness SLA -> reconcile monitor pack -> active data contract ->
    curated analyst checks -> reliability SLAs -> commit

Per the product decision, NO run history is fabricated: checks are created active
and scheduled, but never executed here — operational metrics fill in once the
worker runs them.

Idempotent, keyed on ``Connection.name == entry.source_system`` (names are
unique) *and* the catalog DSN: a same-named connection the catalog didn't create
is never adopted (connect fails 422) and never deleted (disconnect no-ops).
Re-connecting an already-connected entry is a no-op that returns the existing
connection.
"""

from __future__ import annotations

import logging
import random
import sqlite3
from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.catalog.definitions import CatalogEntry, CatalogTable
from app.config import get_settings
from app.connectors.sa import connector_for, dispose_connection, kind_from_dsn
from app.core import check_authoring, contracts, monitors, schema_monitor
from app.core import sla as sla_core
from app.core.audit import audit
from app.core.profiler import profile_dataset
from app.models import utcnow

log = logging.getLogger(__name__)

_IMPORTANCE_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
# check_types the auto monitor pack owns — curated/contract checks must avoid them
# (freshness in particular would otherwise duplicate the monitor on the same column).
_MONITOR_CHECK_TYPES = {"freshness", "row_count_anomaly", "schema_contract", "distribution_drift"}


# --- backing data ----------------------------------------------------------- #

def db_path(entry: CatalogEntry) -> Path:
    ext = "duckdb" if entry.engine == "duckdb" else "sqlite"
    return get_settings().catalog_path / f"{entry.key}.{ext}"


def dsn_for(entry: CatalogEntry) -> str:
    scheme = "duckdb" if entry.engine == "duckdb" else "sqlite"
    return f"{scheme}:///{db_path(entry).as_posix()}"


def generate_backing_data(entry: CatalogEntry, *, force: bool = False) -> Path:
    """Generate the entry's backing DB file (deterministic, seeded). Idempotent:
    skips if the file already exists unless ``force``. The writer is fully closed
    before any read-only connector opens the file (DuckDB allows one writer)."""
    path = db_path(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return path
    if force and path.exists():
        path.unlink()
    rng = random.Random(entry.seed)
    if entry.engine == "duckdb":
        import duckdb

        con = duckdb.connect(str(path))
        try:
            entry.generate(con, rng)
        finally:
            con.close()
    else:
        con = sqlite3.connect(str(path))
        try:
            entry.generate(con, rng)
        finally:
            con.close()
    return path


# --- per-dataset materialization -------------------------------------------- #

def _profile(db: Session, conn: models.Connection, ds: models.Dataset) -> models.Profile:
    settings = get_settings()
    connector = connector_for(conn)
    result = profile_dataset(
        connector, ds.table_name, ds.schema_name, sample_rows=settings.profile_sample_rows
    )
    profile = models.Profile(
        dataset_id=ds.id,
        row_count=result["row_count"],
        sampled_rows=result["sampled_rows"],
        columns=result["columns"],
        table_facts=result["table_facts"],
    )
    ds.row_count = result["row_count"]
    ds.last_profiled_at = utcnow()
    db.add(profile)
    try:
        cols = schema_monitor.introspect_columns(connector, ds.table_name, ds.schema_name)
        schema_monitor.capture_schema_snapshot(db, ds.id, cols, source="profile")
    except Exception:  # noqa: BLE001 - schema capture must never fail seeding
        log.debug("schema snapshot failed for %s", ds.table_name, exc_info=True)
    db.flush()
    return profile


def _apply_knowledge(db: Session, ds: models.Dataset, table: CatalogTable, actor: models.User) -> None:
    b = table.knowledge
    k = models.TableKnowledge(
        business_context=b.business_context,
        known_issues=b.known_issues,
        importance=b.importance,
        owner=b.owner,
        domain=b.domain,
        team=b.team,
        freshness_sla_hours=b.freshness_sla_hours,
        slo_enabled=True,
        pii_columns=list(b.pii_columns),
        notes=b.notes,
        updated_by_id=actor.id,
    )
    ds.knowledge = k  # populate the relationship so reconcile sees the SLA hours
    db.add(k)
    db.flush()
    if b.freshness_sla_hours is not None:
        sla_core.ensure_freshness_sla(db, ds.id, actor.id)


def _build_contract_spec(db: Session, ds: models.Dataset, table: CatalogTable) -> dict:
    """Starter spec from profile/knowledge, enriched with the table's quality
    clauses. The freshness clause is dropped on purpose — the monitor pack owns
    freshness, so a contract freshness clause would create a duplicate check."""
    spec = contracts.default_contract_spec(db, ds)
    spec["freshness"] = {}
    floor = max(1, int((ds.row_count or 0) * 0.5))
    volume = spec.get("volume") or {}
    volume.update({"min_rows": floor, "severity": volume.get("severity", "warn"),
                   "schedule_expr": volume.get("schedule_expr", "1440")})
    spec["volume"] = volume
    spec["quality"] = [
        {
            "id": q.id,
            "name": q.name,
            "check_type": q.check_type,
            "column": q.column,
            "params": dict(q.params),
            "severity": q.severity,
            "schedule_expr": q.schedule_expr,
            "rationale": q.rationale,
        }
        for q in table.quality
    ]
    spec["owner"] = {"name": table.knowledge.owner, "importance": table.knowledge.importance}
    spec["consumers"] = [{"name": c} for c in table.consumers]
    spec["terms"] = table.contract_terms or table.knowledge.business_context
    return spec


def _apply_contract(db: Session, ds: models.Dataset, table: CatalogTable, actor: models.User) -> None:
    spec = _build_contract_spec(db, ds, table)
    contract = models.DataContract(
        dataset_id=ds.id,
        name=f"{ds.table_name} contract",
        version="1.0.0",
        status="draft",
        spec=spec,
        created_by_id=actor.id,
    )
    db.add(contract)
    db.flush()
    contracts.snapshot_version(db, contract, actor)
    contracts.apply_contract(db, contract, actor)  # activates + materializes clause checks


def _apply_extra_checks(db: Session, ds: models.Dataset, table: CatalogTable, actor: models.User) -> None:
    for ec in table.extra_checks:
        if ec.check_type in _MONITOR_CHECK_TYPES:  # defensive: never duplicate a monitor
            log.warning("skipping catalog extra check %s with monitor type %s", ec.name, ec.check_type)
            continue
        check_authoring.create_check(
            db, actor, ds,
            name=ec.name,
            check_type=ec.check_type,
            column_name=ec.column_name,
            params=dict(ec.params),
            severity=ec.severity,
            rationale=ec.rationale,
            schedule_kind="interval",
            schedule_expr=ec.schedule_expr,
            status="active",
            origin="manual",
        )


def _apply_slas(db: Session, ds: models.Dataset, table: CatalogTable, actor: models.User) -> None:
    for s in table.slas:
        sla_core.create_sla_definition(
            db, actor,
            name=s.name,
            scope="dataset",
            scope_id=ds.id,
            target_type=s.target_type,
            objective=s.objective,
            window=s.window,
            enabled=True,
        )


# --- public API ------------------------------------------------------------- #

def _connection_named(db: Session, name: str) -> models.Connection | None:
    return db.query(models.Connection).filter(models.Connection.name == name).first()


def find_connection(db: Session, entry: CatalogEntry) -> models.Connection | None:
    """The entry's catalog-owned connection, or None. Ownership is verified by
    DSN (the deterministic catalog backing-file path), not name alone, so a
    user connection that happens to share the source-system name is never
    adopted as "already connected" — and never deleted by disconnect."""
    conn = _connection_named(db, entry.source_system)
    if conn is not None and conn.dsn != dsn_for(entry):
        return None
    return conn


def connect_entry(db: Session, entry: CatalogEntry, actor: models.User) -> models.Connection:
    """Materialize the whole governed dataset for ``entry``. Idempotent: returns
    the existing connection if already connected. Commits once; rolls back the
    entire estate on any failure (no partial catalog entry). Raises ValueError on
    a generation/validation failure or a connection-name collision (the API
    surfaces it as 422)."""
    existing = find_connection(db, entry)
    if existing is not None:
        return existing
    if _connection_named(db, entry.source_system) is not None:
        # Same name, different DSN: a connection the catalog does not own.
        # Fail loudly rather than adopting it (idempotent-reconnect would report
        # success against foreign data, and disconnect would cascade-delete it).
        raise ValueError(
            f"Connection name '{entry.source_system}' is already in use by a connection "
            "that was not created from the catalog. Rename or remove that connection first."
        )

    dsn = dsn_for(entry)
    conn: models.Connection | None = None
    try:
        generate_backing_data(entry)
        conn = models.Connection(
            name=entry.source_system,
            kind=kind_from_dsn(dsn),
            dsn=dsn,
            created_by_id=actor.id,
        )
        db.add(conn)
        db.flush()  # assign conn.id (connector_for + audit need it)
        audit(db, actor, "connection.create", "connection", conn.id,
              name=conn.name, kind=conn.kind, source="catalog", catalog_key=entry.key)

        datasets: list[tuple[models.Dataset, CatalogTable]] = []
        for table in entry.tables:
            ds = models.Dataset(
                connection_id=conn.id,
                schema_name=None,
                table_name=table.table_name,
                display_name=table.display_name or table.table_name,
            )
            db.add(ds)
            db.flush()
            monitors.ensure_monitor_pack(db, ds)
            datasets.append((ds, table))

        for ds, table in datasets:
            profile = _profile(db, conn, ds)
            _apply_knowledge(db, ds, table, actor)
            monitors.reconcile_monitor_pack(db, ds, profile, actor_id=actor.id)
            _apply_contract(db, ds, table, actor)
            _apply_extra_checks(db, ds, table, actor)
            _apply_slas(db, ds, table, actor)

        db.commit()
        db.refresh(conn)
        return conn
    except Exception as exc:  # noqa: BLE001 - turn any seeding failure into a clean rollback
        db.rollback()
        if conn is not None and conn.id is not None:
            dispose_connection(conn.id)
        log.exception("Failed to connect catalog entry %s", entry.key)
        raise ValueError(f"Could not connect '{entry.title}': {exc}") from exc


def disconnect_entry(db: Session, entry: CatalogEntry, actor: models.User) -> bool:
    """Remove the connection and all dependents for ``entry``. Leaves the
    generated backing file (gitignored) so a later re-connect is fast. Returns
    False if the entry was not connected."""
    from app.core.deletion import cleanup_dataset_dependents

    conn = find_connection(db, entry)
    if conn is None:
        return False
    conn_id = conn.id
    audit(db, actor, "connection.delete", "connection", conn.id,
          name=conn.name, kind=conn.kind, source="catalog", catalog_key=entry.key)
    for ds in list(conn.datasets):
        cleanup_dataset_dependents(db, ds.id)
    db.query(models.ConnectionGrant).filter(
        models.ConnectionGrant.connection_id == conn_id
    ).delete(synchronize_session=False)
    db.delete(conn)  # cascades to datasets/checks/profiles/knowledge/contracts via ORM
    db.commit()
    dispose_connection(conn_id)
    return True


def entry_status(db: Session, entry: CatalogEntry) -> dict:
    """Preview + connected-state for one catalog entry (drives the API list)."""
    conn = find_connection(db, entry)
    importance = max(
        (t.knowledge.importance for t in entry.tables),
        key=lambda i: _IMPORTANCE_RANK.get(i, 1),
        default="medium",
    )
    pii = any(t.knowledge.pii_columns for t in entry.tables)
    owner = entry.tables[0].knowledge.owner if entry.tables else ""
    table_previews = [
        {
            "table_name": t.table_name,
            "importance": t.knowledge.importance,
            "pii": bool(t.knowledge.pii_columns),
            "freshness_sla_hours": t.knowledge.freshness_sla_hours,
        }
        for t in entry.tables
    ]
    if conn is not None:
        ds_ids = [d.id for d in conn.datasets]
        check_count = (
            db.query(models.Check)
            .filter(models.Check.dataset_id.in_(ds_ids), models.Check.status == "active")
            .count()
            if ds_ids
            else 0
        )
        has_contract = bool(
            ds_ids
            and db.query(models.DataContract)
            .filter(models.DataContract.dataset_id.in_(ds_ids),
                    models.DataContract.status == "active")
            .first()
        )
        table_count = len(ds_ids)
    else:
        # planned counts before connecting: governance clauses + curated checks
        # (the auto monitor pack adds freshness/volume/schema/drift on top).
        check_count = sum(len(t.quality) + len(t.extra_checks) for t in entry.tables)
        has_contract = any(t.quality for t in entry.tables)
        table_count = len(entry.tables)

    return {
        "key": entry.key,
        "title": entry.title,
        "description": entry.description,
        "domain": entry.domain,
        "source_system": entry.source_system,
        "engine": entry.engine,
        "tags": list(entry.tags),
        "connected": conn is not None,
        "connection_id": conn.id if conn is not None else None,
        "table_count": table_count,
        "check_count": check_count,
        "has_contract": has_contract,
        "pii": pii,
        "owner": owner,
        "importance": importance,
        "tables": table_previews,
    }
