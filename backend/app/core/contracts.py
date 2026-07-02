"""Data contract normalization, ODCS mapping, enforcement, and conformance (#105)."""

from __future__ import annotations

import copy
import difflib
import json
import re
from collections.abc import Iterable
from typing import Any

import yaml
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.api.serialize import check_out
from app.connectors.sa import connector_for
from app.core import schema_monitor
from app.core.check_types import validate_check
from app.models import utcnow

CONTRACT_SPEC_VERSION = 1
MARKER_RE = re.compile(r"\[contract:(?P<contract_id>\d+):clause:(?P<clause>[^\]]+)\]")


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or fallback


def _column_map(columns: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(c.get("name") or "").lower(): c for c in columns if c.get("name")}


def _latest_profile(db: Session, dataset_id: int) -> models.Profile | None:
    return (
        db.query(models.Profile)
        .filter(models.Profile.dataset_id == dataset_id)
        .order_by(models.Profile.id.desc())
        .first()
    )


def default_contract_spec(db: Session, dataset: models.Dataset) -> dict[str, Any]:
    """Build a useful starter contract from profile, source schema, and knowledge."""
    profile = _latest_profile(db, dataset.id)
    if profile is not None:
        columns = [
            {
                "name": c.get("name"),
                "dtype": c.get("dtype") or "",
                "nullable": c.get("null_count", 0) > 0,
                "required": True,
            }
            for c in profile.columns or []
            if c.get("name")
        ]
    else:
        connector = connector_for(dataset.connection)
        columns = [
            {
                "name": c["name"],
                "dtype": c.get("dtype") or "",
                "nullable": bool(c.get("nullable", True)),
                "required": True,
            }
            for c in connector.get_columns(dataset.table_name, dataset.schema_name)
        ]

    knowledge = dataset.knowledge
    temporal = ""
    if profile is not None:
        temporal_cols = (profile.table_facts or {}).get("temporal_columns") or []
        if temporal_cols:
            temporal = str(temporal_cols[0].get("name") or "")
    if not temporal:
        for col in columns:
            dtype = str(col.get("dtype") or "").lower()
            if any(token in dtype for token in ("date", "time", "timestamp")):
                temporal = str(col["name"])
                break

    spec: dict[str, Any] = {
        "version": CONTRACT_SPEC_VERSION,
        "schema": {
            "columns": columns,
            "allow_extra_columns": True,
            "compare_types": False,
            "enforce_nullable": False,
        },
        "quality": [],
        "owner": {
            "name": knowledge.owner if knowledge else "",
            "importance": knowledge.importance if knowledge else "medium",
        },
        "consumers": [],
        "terms": "",
    }
    if knowledge and knowledge.freshness_sla_hours and temporal:
        spec["freshness"] = {
            "column": temporal,
            "max_age_hours": knowledge.freshness_sla_hours,
            "severity": "error",
            "schedule_expr": "360",
        }
    if profile is not None and profile.row_count > 0:
        spec["volume"] = {
            "min_rows": 1,
            "severity": "warn",
            "schedule_expr": "1440",
            "baseline_rows": profile.row_count,
        }
    return normalize_spec(spec)


_VALID_SEVERITIES = ("info", "warn", "error")


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _coerce_int_opt(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_severity(value: Any, default: str) -> str:
    """Clamp to a valid Check severity. An ODCS/UI spec may carry 'high'/'critical';
    persisting those unvalidated later 500s CheckOut/IncidentOut serialization for
    everyone and drops the check below 'info' in alert ranking (#A2)."""
    v = str(value or default).strip().lower()
    return v if v in _VALID_SEVERITIES else default


def _coerce_interval_expr(value: Any, default: str) -> str:
    """Contract checks run on interval (minutes) schedules. A non-numeric expr would
    raise in the scheduler's compute_next_run and wedge the whole poll loop (#A1), so
    fall back to the default rather than persist something unparseable."""
    try:
        int(float(value))
        return str(value)
    except (TypeError, ValueError):
        return default


def normalize_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Return the normalized internal contract shape.

    The function is deliberately tolerant: unsupported ODCS-ish or UI fields are
    preserved where harmless, while the clauses DQ Sentinel enforces are shaped
    consistently.
    """
    raw = copy.deepcopy(spec or {})
    out: dict[str, Any] = {
        "version": _coerce_int(raw.get("version") or CONTRACT_SPEC_VERSION, CONTRACT_SPEC_VERSION),
        "schema": {
            "columns": [],
            "allow_extra_columns": bool(raw.get("schema", {}).get("allow_extra_columns", True))
            if isinstance(raw.get("schema"), dict)
            else True,
            "compare_types": bool(raw.get("schema", {}).get("compare_types", False))
            if isinstance(raw.get("schema"), dict)
            else False,
            "enforce_nullable": bool(raw.get("schema", {}).get("enforce_nullable", False))
            if isinstance(raw.get("schema"), dict)
            else False,
        },
        "freshness": {},
        "volume": {},
        "quality": [],
        "owner": raw.get("owner") if isinstance(raw.get("owner"), dict) else {},
        "consumers": raw.get("consumers") if isinstance(raw.get("consumers"), list) else [],
        "terms": raw.get("terms") or "",
    }

    schema = raw.get("schema") or {}
    raw_columns = schema.get("columns") if isinstance(schema, dict) else schema
    if isinstance(raw_columns, list):
        for col in raw_columns:
            if not isinstance(col, dict) or not col.get("name"):
                continue
            out["schema"]["columns"].append(
                {
                    "name": str(col["name"]),
                    "dtype": str(col.get("dtype") or col.get("type") or ""),
                    "nullable": bool(col.get("nullable", True)),
                    "required": bool(col.get("required", True)),
                    "description": str(col.get("description") or ""),
                }
            )

    freshness = raw.get("freshness") or {}
    if isinstance(freshness, dict) and freshness.get("column"):
        out["freshness"] = {
            "column": str(freshness["column"]),
            "max_age_hours": _coerce_float(
                freshness.get("max_age_hours") or freshness.get("threshold_hours") or 24, 24.0
            ),
            "severity": _coerce_severity(freshness.get("severity"), "error"),
            "schedule_expr": _coerce_interval_expr(freshness.get("schedule_expr"), "360"),
        }

    volume = raw.get("volume") or {}
    if isinstance(volume, dict) and (
        volume.get("min_rows") is not None or volume.get("baseline_rows") is not None
    ):
        normalized_volume: dict[str, Any] = {
            "severity": _coerce_severity(volume.get("severity"), "warn"),
            "schedule_expr": _coerce_interval_expr(volume.get("schedule_expr"), "1440"),
        }
        min_rows = _coerce_int_opt(volume.get("min_rows"))
        if min_rows is not None:
            normalized_volume["min_rows"] = min_rows
        baseline_rows = _coerce_int_opt(volume.get("baseline_rows"))
        if baseline_rows is not None:
            normalized_volume["baseline_rows"] = baseline_rows
        out["volume"] = normalized_volume

    for idx, q in enumerate(raw.get("quality") or []):
        if not isinstance(q, dict):
            continue
        check_type = q.get("check_type") or q.get("type")
        if not check_type:
            continue
        label = q.get("name") or q.get("label") or str(check_type)
        out["quality"].append(
            {
                "id": q.get("id") or _slug(label, f"quality-{idx + 1}"),
                "name": label,
                "check_type": str(check_type),
                "column": q.get("column") or q.get("column_name"),
                "params": q.get("params") if isinstance(q.get("params"), dict) else {},
                "severity": _coerce_severity(q.get("severity"), "error"),
                "schedule_expr": _coerce_interval_expr(q.get("schedule_expr"), "1440"),
                "rationale": q.get("rationale") or q.get("description") or "",
            }
        )

    if isinstance(raw.get("materialized"), dict):
        out["materialized"] = raw["materialized"]
    return out


def snapshot_version(
    db: Session, contract: models.DataContract, user: models.User | None
) -> models.DataContractVersion:
    version = models.DataContractVersion(
        contract_id=contract.id,
        version=contract.version,
        spec=copy.deepcopy(contract.spec or {}),
        created_by_id=getattr(user, "id", None),
    )
    db.add(version)
    return version


def contract_out(contract: models.DataContract) -> dict[str, Any]:
    return {
        "id": contract.id,
        "dataset_id": contract.dataset_id,
        "name": contract.name,
        "version": contract.version,
        "status": contract.status,
        "spec": contract.spec or {},
        "created_by_id": contract.created_by_id,
        "created_at": contract.created_at,
        "activated_at": contract.activated_at,
        "version_count": len(contract.versions or []),
    }


def marker(contract_id: int, clause_id: str) -> str:
    return f"[contract:{contract_id}:clause:{clause_id}]"


def _materialized_check_items(spec: dict[str, Any] | None) -> list[dict[str, Any]]:
    materialized = (spec or {}).get("materialized")
    if not isinstance(materialized, dict):
        return []
    checks = materialized.get("checks")
    if not isinstance(checks, list):
        return []
    return [item for item in checks if isinstance(item, dict)]


def _schema_monitor_columns(spec: dict[str, Any]) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    for idx, col in enumerate((spec.get("schema") or {}).get("columns") or []):
        if not col.get("name") or not col.get("required", True):
            continue
        columns.append(
            {
                "name": str(col["name"]),
                "dtype": str(col.get("dtype") or ""),
                "nullable": bool(col.get("nullable", True)),
                "ordinal": idx,
            }
        )
    return columns


def iter_materialized_check_specs(contract: models.DataContract) -> list[dict[str, Any]]:
    spec = normalize_spec(contract.spec or {})
    checks: list[dict[str, Any]] = []
    schema = spec.get("schema") or {}
    if _schema_monitor_columns(spec):
        checks.append(
            {
                "clause_id": "schema:change",
                "kind": "schema",
                "label": "Schema matches contract",
                "check_type": "schema_change",
                "column_name": None,
                "params": {
                    "baseline": "pinned",
                    "on_removed": True,
                    "on_type_change": bool(schema.get("compare_types", False)),
                    "on_nullability_change": bool(schema.get("enforce_nullable", False)),
                    "on_added": not bool(schema.get("allow_extra_columns", True)),
                    "on_reorder": False,
                    "ignore_columns": [],
                },
                "severity": "error",
                "schedule_expr": "1440",
                "rationale": "Contract schema drift monitor.",
            }
        )
    freshness = spec.get("freshness") or {}
    if freshness.get("column"):
        checks.append(
            {
                "clause_id": "freshness",
                "kind": "freshness",
                "label": f"Freshness within {freshness.get('max_age_hours')}h",
                "check_type": "freshness",
                "column_name": freshness["column"],
                "params": {"max_age_hours": freshness.get("max_age_hours", 24)},
                "severity": freshness.get("severity") or "error",
                "schedule_expr": freshness.get("schedule_expr") or "360",
            }
        )
    volume = spec.get("volume") or {}
    if volume.get("min_rows") is not None:
        checks.append(
            {
                "clause_id": "volume:row_count_min",
                "kind": "volume",
                "label": f"At least {volume.get('min_rows')} rows",
                "check_type": "row_count_min",
                "column_name": None,
                "params": {"min_rows": volume["min_rows"]},
                "severity": volume.get("severity") or "warn",
                "schedule_expr": volume.get("schedule_expr") or "1440",
            }
        )
    for q in spec.get("quality") or []:
        clause_id = f"quality:{q['id']}"
        checks.append(
            {
                "clause_id": clause_id,
                "kind": "quality",
                "label": str(q.get("name") or q.get("check_type")),
                "check_type": q["check_type"],
                "column_name": q.get("column"),
                "params": q.get("params") or {},
                "severity": q.get("severity") or "error",
                "schedule_expr": q.get("schedule_expr") or "1440",
                "rationale": q.get("rationale") or "",
            }
        )
    return checks


def _existing_materialized_check(
    db: Session, contract: models.DataContract, clause_id: str
) -> models.Check | None:
    for item in _materialized_check_items(contract.spec):
        if item.get("clause_id") == clause_id and item.get("check_id"):
            try:
                check_id = int(item["check_id"])
            except (TypeError, ValueError):
                continue
            check = db.get(models.Check, check_id)
            if check is not None and check.dataset_id == contract.dataset_id:
                return check
    mark = marker(contract.id, clause_id)
    return (
        db.query(models.Check)
        .filter(
            models.Check.dataset_id == contract.dataset_id,
            models.Check.status != "archived",
            models.Check.rationale.like(f"%{mark}%"),
        )
        .order_by(models.Check.id.desc())
        .first()
    )


def archive_contract_checks(
    db: Session,
    contract: models.DataContract,
    keep_check_ids: set[int] | None = None,
) -> list[models.Check]:
    """Archive checks materialized by a contract that is no longer enforceable."""
    keep = keep_check_ids or set()
    check_ids: set[int] = set()
    for item in _materialized_check_items(contract.spec):
        if item.get("check_id"):
            try:
                check_ids.add(int(item["check_id"]))
            except (TypeError, ValueError):
                continue

    checks_by_id: dict[int, models.Check] = {}
    if check_ids:
        for check in db.query(models.Check).filter(models.Check.id.in_(check_ids)).all():
            if check.dataset_id == contract.dataset_id:
                checks_by_id[check.id] = check

    mark = f"[contract:{contract.id}:clause:"
    for check in (
        db.query(models.Check)
        .filter(
            models.Check.dataset_id == contract.dataset_id,
            models.Check.status != "archived",
            models.Check.rationale.like(f"%{mark}%"),
        )
        .all()
    ):
        checks_by_id[check.id] = check

    archived: list[models.Check] = []
    for check in checks_by_id.values():
        if check.id in keep:
            continue
        if check.status != "archived":
            check.status = "archived"
            check.next_run_at = None
            archived.append(check)
    return archived


def apply_contract(
    db: Session, contract: models.DataContract, user: models.User
) -> tuple[list[models.Check], list[models.Check], bool]:
    """Activate a contract and materialize enforceable clauses as checks.

    Schema enforcement is evaluated live in conformance and as a scheduled
    ``schema_change`` check against a pinned baseline derived from the contract.
    """
    spec = normalize_spec(contract.spec or {})
    now = utcnow()
    created: list[models.Check] = []
    updated: list[models.Check] = []
    materialized_checks: list[dict[str, Any]] = []
    schema_columns = _schema_monitor_columns(spec)
    schema_pinned = False
    if schema_columns:
        schema_monitor.pin_baseline(db, contract.dataset_id, schema_columns)
        schema_pinned = True

    for desired in iter_materialized_check_specs(contract):
        try:
            params = validate_check(
                desired["check_type"], desired.get("column_name"), desired.get("params") or {}
            )
        except ValueError as exc:
            raise ValueError(f"{desired['clause_id']}: {exc}") from exc

        check = _existing_materialized_check(db, contract, desired["clause_id"])
        rationale = (
            f"{marker(contract.id, desired['clause_id'])} "
            f"{desired.get('rationale') or 'Materialized from data contract.'}"
        ).strip()
        if check is None:
            check = models.Check(
                dataset_id=contract.dataset_id,
                name=f"[Contract] {contract.name}: {desired['label']}",
                check_type=desired["check_type"],
                column_name=desired.get("column_name"),
                params=params,
                severity=desired["severity"],
                rationale=rationale,
                schedule_kind="interval",
                schedule_expr=desired["schedule_expr"],
                status="active",
                origin="contract",
                created_by_id=user.id,
                next_run_at=now,
            )
            db.add(check)
            db.flush()
            created.append(check)
        else:
            check.name = f"[Contract] {contract.name}: {desired['label']}"
            check.check_type = desired["check_type"]
            check.column_name = desired.get("column_name")
            check.params = params
            check.severity = desired["severity"]
            check.rationale = rationale
            check.schedule_kind = "interval"
            check.schedule_expr = desired["schedule_expr"]
            if check.status != "active":
                check.status = "active"
                check.next_run_at = now
            updated.append(check)
        materialized_checks.append(
            {
                "clause_id": desired["clause_id"],
                "kind": desired["kind"],
                "check_id": check.id,
                "check_type": check.check_type,
            }
        )

    archive_contract_checks(
        db,
        contract,
        keep_check_ids={
            int(item["check_id"]) for item in materialized_checks if item.get("check_id")
        },
    )
    spec["materialized"] = {
        "checks": materialized_checks,
        "schema": {"mode": "live_and_schema_change", "pinned": schema_pinned},
        "activated_at": now.isoformat(),
    }

    for other in (
        db.query(models.DataContract)
        .filter(
            models.DataContract.dataset_id == contract.dataset_id,
            models.DataContract.id != contract.id,
            models.DataContract.status == "active",
        )
        .all()
    ):
        archive_contract_checks(db, other)
        other.status = "deprecated"
    contract.spec = spec
    contract.status = "active"
    contract.activated_at = now
    snapshot_version(db, contract, user)
    return created, updated, schema_pinned


def _latest_run_by_check(db: Session, check_ids: list[int]) -> dict[int, models.CheckRun]:
    if not check_ids:
        return {}
    latest = (
        db.query(models.CheckRun.check_id, func.max(models.CheckRun.id).label("last_id"))
        .filter(models.CheckRun.check_id.in_(check_ids))
        .group_by(models.CheckRun.check_id)
        .subquery()
    )
    rows = db.query(models.CheckRun).join(latest, models.CheckRun.id == latest.c.last_id).all()
    return {r.check_id: r for r in rows}


def _schema_conformance(contract: models.DataContract) -> dict[str, Any]:
    spec = normalize_spec(contract.spec or {})
    schema = spec.get("schema") or {}
    expected = schema.get("columns") or []
    if not expected:
        return {
            "clause_id": "schema",
            "kind": "schema",
            "label": "Schema",
            "status": "unknown",
            "detail": "No schema columns are declared in this contract.",
            "expected": {},
            "observed": {},
        }

    connector = connector_for(contract.dataset.connection)
    current = connector.get_columns(contract.dataset.table_name, contract.dataset.schema_name)
    current_by_name = _column_map(current)
    missing: list[str] = []
    type_mismatches: list[str] = []
    nullability_mismatches: list[str] = []
    for col in expected:
        name = str(col["name"])
        observed = current_by_name.get(name.lower())
        if observed is None:
            if col.get("required", True):
                missing.append(name)
            continue
        if schema.get("compare_types"):
            want = str(col.get("dtype") or "").lower()
            got = str(observed.get("dtype") or "").lower()
            if want and got and want != got:
                type_mismatches.append(f"{name}: expected {want}, got {got}")
        if schema.get("enforce_nullable"):
            want_nullable = bool(col.get("nullable", True))
            got_nullable = bool(observed.get("nullable", True))
            if want_nullable != got_nullable:
                nullability_mismatches.append(
                    f"{name}: expected nullable={want_nullable}, got nullable={got_nullable}"
                )
    expected_names = {str(c["name"]).lower() for c in expected}
    extra = [c["name"] for c in current if str(c.get("name") or "").lower() not in expected_names]
    if schema.get("allow_extra_columns", True):
        extra = []
    problems = missing + type_mismatches + nullability_mismatches + [f"extra column {c}" for c in extra]
    return {
        "clause_id": "schema",
        "kind": "schema",
        "label": "Schema",
        "status": "breached" if problems else "pass",
        "detail": "; ".join(problems) if problems else "Current schema conforms to the contract.",
        "expected": {"columns": expected, "allow_extra_columns": schema.get("allow_extra_columns", True)},
        "observed": {"columns": current},
    }


def conformance(db: Session, contract: models.DataContract) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [_schema_conformance(contract)]
    desired = iter_materialized_check_specs(contract)
    materialized: dict[str, int] = {}
    for item in _materialized_check_items(contract.spec):
        if not item.get("clause_id") or not item.get("check_id"):
            continue
        try:
            materialized[str(item["clause_id"])] = int(item["check_id"])
        except (TypeError, ValueError):
            continue
    check_ids = list(materialized.values())
    runs = _latest_run_by_check(db, check_ids)

    for item in desired:
        check_id = materialized.get(item["clause_id"])
        run = runs.get(check_id) if check_id else None
        if run is None:
            status = "unknown"
            detail = "No check run has completed for this contract clause."
            run_status = None
        else:
            run_status = run.status
            status = "pass" if run.status == "pass" else "breached"
            detail = run.error_message or (
                f"Latest run {run.status} with {run.violation_count} violation(s)."
            )
        clauses.append(
            {
                "clause_id": item["clause_id"],
                "kind": item["kind"],
                "label": item["label"],
                "status": status,
                "check_id": check_id,
                "check_status": run_status,
                "detail": detail,
                "expected": {
                    "check_type": item["check_type"],
                    "column_name": item.get("column_name"),
                    "params": item.get("params") or {},
                },
                "observed": {
                    "run_id": run.id if run else None,
                    "violation_count": run.violation_count if run else None,
                    "metrics": run.metrics if run else {},
                },
            }
        )

    if any(c["status"] == "breached" for c in clauses):
        overall = "breached"
    elif any(c["status"] == "unknown" for c in clauses):
        overall = "unknown"
    else:
        overall = "pass"
    return {
        "contract_id": contract.id,
        "dataset_id": contract.dataset_id,
        "status": overall,
        "clauses": clauses,
        "generated_at": utcnow(),
    }


def _odcs_columns(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": c["name"],
            "physicalType": c.get("dtype") or "",
            "required": bool(c.get("required", True)),
            "nullable": bool(c.get("nullable", True)),
            "description": c.get("description") or "",
        }
        for c in (spec.get("schema") or {}).get("columns") or []
    ]


def to_odcs_yaml(contract: models.DataContract) -> str:
    spec = normalize_spec(contract.spec or {})
    data: dict[str, Any] = {
        "apiVersion": "v3.0.0",
        "kind": "DataContract",
        "name": contract.name,
        "version": contract.version,
        "status": contract.status,
        "dataset": {
            "schema": contract.dataset.schema_name,
            "table": contract.dataset.table_name,
        },
        "schema": _odcs_columns(spec),
        "quality": [],
        "team": spec.get("owner") or {},
        "stakeholders": spec.get("consumers") or [],
        "terms": spec.get("terms") or "",
    }
    freshness = spec.get("freshness") or {}
    if freshness:
        data.setdefault("slaProperties", []).append(
            {
                "property": "freshness",
                "column": freshness.get("column"),
                "threshold": f"PT{float(freshness.get('max_age_hours', 24)):g}H",
                "maxAgeHours": freshness.get("max_age_hours"),
                "severity": freshness.get("severity"),
            }
        )
    volume = spec.get("volume") or {}
    if volume:
        data.setdefault("slaProperties", []).append(
            {
                "property": "row_count",
                "minRows": volume.get("min_rows"),
                "baselineRows": volume.get("baseline_rows"),
                "severity": volume.get("severity"),
            }
        )
    for q in spec.get("quality") or []:
        data["quality"].append(
            {
                "id": q.get("id"),
                "name": q.get("name"),
                "type": q.get("check_type"),
                "column": q.get("column"),
                "severity": q.get("severity"),
                "params": q.get("params") or {},
                "description": q.get("rationale") or "",
            }
        )
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=False)


def _duration_hours(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().upper()
    match = re.fullmatch(r"PT(\d+(?:\.\d+)?)H", s)
    if match:
        return float(match.group(1))
    match = re.fullmatch(r"P(\d+(?:\.\d+)?)D", s)
    if match:
        return float(match.group(1)) * 24
    try:
        return float(s)
    except ValueError:
        return None


def from_odcs_yaml(raw_yaml: str) -> tuple[str, str, dict[str, Any]]:
    data = yaml.safe_load(raw_yaml) or {}
    if not isinstance(data, dict):
        raise ValueError("ODCS YAML must be a mapping")
    name = str(data.get("name") or data.get("id") or "Data contract")
    version = str(data.get("version") or "0.1.0")

    schema_block = data.get("schema") or data.get("schemaProperties") or []
    if isinstance(schema_block, dict):
        schema_columns = schema_block.get("columns") or schema_block.get("properties") or []
        allow_extra = bool(schema_block.get("allowExtraColumns", schema_block.get("allow_extra_columns", True)))
    else:
        schema_columns = schema_block
        allow_extra = True
    columns = []
    for col in schema_columns if isinstance(schema_columns, list) else []:
        if not isinstance(col, dict) or not col.get("name"):
            continue
        columns.append(
            {
                "name": str(col["name"]),
                "dtype": str(col.get("physicalType") or col.get("logicalType") or col.get("type") or ""),
                "required": bool(col.get("required", True)),
                "nullable": bool(col.get("nullable", True)),
                "description": str(col.get("description") or ""),
            }
        )

    spec: dict[str, Any] = {
        "version": CONTRACT_SPEC_VERSION,
        "schema": {"columns": columns, "allow_extra_columns": allow_extra},
        "quality": [],
        "owner": data.get("team") if isinstance(data.get("team"), dict) else {},
        "consumers": data.get("stakeholders") if isinstance(data.get("stakeholders"), list) else [],
        "terms": data.get("terms") or "",
    }

    for sla in data.get("slaProperties") or data.get("sla") or []:
        if not isinstance(sla, dict):
            continue
        prop = str(sla.get("property") or sla.get("type") or "").lower()
        if prop in {"freshness", "freshness_sla"}:
            hours = sla.get("maxAgeHours") or _duration_hours(sla.get("threshold"))
            spec["freshness"] = {
                "column": sla.get("column") or sla.get("field") or "",
                "max_age_hours": hours or 24,
                "severity": sla.get("severity") or "error",
                "schedule_expr": str(sla.get("schedule_expr") or "360"),
            }
        elif prop in {"row_count", "volume", "volume_sla"}:
            spec["volume"] = {
                "min_rows": int(sla.get("minRows") or sla.get("min_rows") or 1),
                "baseline_rows": sla.get("baselineRows") or sla.get("baseline_rows"),
                "severity": sla.get("severity") or "warn",
                "schedule_expr": str(sla.get("schedule_expr") or "1440"),
            }

    for idx, q in enumerate(data.get("quality") or data.get("qualityRules") or []):
        if not isinstance(q, dict):
            continue
        check_type = q.get("type") or q.get("check_type")
        if not check_type:
            continue
        label = str(q.get("name") or check_type)
        spec["quality"].append(
            {
                "id": q.get("id") or _slug(label, f"quality-{idx + 1}"),
                "name": label,
                "check_type": check_type,
                "column": q.get("column") or q.get("field"),
                "params": q.get("params") if isinstance(q.get("params"), dict) else {},
                "severity": q.get("severity") or "error",
                "rationale": q.get("description") or "",
            }
        )
    return name, version, normalize_spec(spec)


def spec_diff(from_spec: dict[str, Any], to_spec: dict[str, Any]) -> dict[str, list[str]]:
    before = json.dumps(normalize_spec(from_spec), indent=2, sort_keys=True).splitlines()
    after = json.dumps(normalize_spec(to_spec), indent=2, sort_keys=True).splitlines()
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    for line in difflib.unified_diff(before, after, fromfile="before", tofile="after", lineterm=""):
        if line.startswith("@@"):
            changed.append(line)
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
    return {"added": added, "removed": removed, "changed": changed}


def serialize_checks(checks: Iterable[models.Check], dataset_name: str) -> list[Any]:
    return [check_out(c, dataset_name) for c in checks]
