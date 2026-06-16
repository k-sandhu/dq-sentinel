"""Automatic monitor-pack enrollment and reconciliation (issue #115)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app import models, schemas
from app.core.check_types import CHECK_TYPES, validate_check
from app.models import utcnow

PACK_VERSION = 1
MONITOR_KINDS = ("freshness", "volume", "schema", "drift")
ID_HINTS = ("id", "key", "code", "sku", "uuid")

DEFAULT_MONITOR_PACK_CONFIG: dict[str, Any] = {
    "version": PACK_VERSION,
    "monitors": {
        "freshness": True,
        "volume": True,
        "schema": True,
        "drift": True,
    },
    "cadence": {
        "freshness_minutes": 360,
        "volume_minutes": 1440,
        "schema_minutes": 360,
        "drift_minutes": 1440,
    },
    "sensitivity": {
        "freshness_max_age_hours": 48,
        "volume_sigma": 3.0,
        "volume_lookback_runs": 14,
        "volume_min_history": 5,
        "drift_threshold": 0.2,
    },
    "limits": {
        "max_drift_checks": 4,
    },
    "overrides": {},
}


@dataclass(frozen=True)
class MonitorSpec:
    kind: str
    check_type: str
    column_name: str | None
    params: dict[str, Any]
    severity: str
    name: str
    rationale: str
    schedule_minutes: int


def default_monitor_pack_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_MONITOR_PACK_CONFIG)


def normalize_monitor_pack_config(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = _deep_merge(default_monitor_pack_config(), config or {})
    merged["version"] = PACK_VERSION
    return merged


def ensure_monitor_pack(db: Session, dataset: models.Dataset) -> models.DatasetMonitorPack:
    pack = (
        db.query(models.DatasetMonitorPack)
        .filter(models.DatasetMonitorPack.dataset_id == dataset.id)
        .first()
    )
    if pack is None:
        pack = models.DatasetMonitorPack(
            dataset_id=dataset.id,
            enabled=True,
            config=default_monitor_pack_config(),
            status="pending_profile",
        )
        db.add(pack)
        db.flush()
    elif not pack.config:
        pack.config = default_monitor_pack_config()
        db.flush()
    return pack


def latest_profile(db: Session, dataset_id: int) -> models.Profile | None:
    return (
        db.query(models.Profile)
        .filter(models.Profile.dataset_id == dataset_id)
        .order_by(models.Profile.id.desc())
        .first()
    )


def reconcile_monitor_pack(
    db: Session,
    dataset: models.Dataset,
    profile: models.Profile | None = None,
    *,
    actor_id: int | None = None,
) -> schemas.MonitorPackOut:
    """Create/update system-managed checks for a dataset monitor pack.

    The caller owns the transaction. This function stages changes, flushes IDs so
    check metadata can reference the pack id, and returns the response shape.
    """
    pack = ensure_monitor_pack(db, dataset)
    config = normalize_monitor_pack_config(pack.config)
    if pack.config != config:
        pack.config = config

    if not pack.enabled:
        disabled = _disable_managed_checks(db, dataset.id)
        result = schemas.MonitorPackReconciliationOut(
            status="disabled",
            disabled=disabled,
            message="Monitor pack is disabled; managed checks are disabled.",
        )
        _record_result(pack, result)
        db.flush()
        return monitor_pack_out(db, pack, result)

    if profile is None:
        profile = latest_profile(db, dataset.id)
    if profile is None:
        result = _pending_profile_result(config)
        _record_result(pack, result)
        db.flush()
        return monitor_pack_out(db, pack, result)

    specs, skipped = _build_monitor_specs(dataset, profile, config)
    created = 0
    updated = 0
    disabled = 0
    desired_keys: set[tuple[str, str]] = set()
    managed = _managed_checks(db, dataset.id)
    managed_by_key: dict[tuple[str, str], models.Check] = {}

    for check in managed:
        key = _managed_key(check)
        if key is None:
            continue
        if key not in managed_by_key:
            managed_by_key[key] = check
        elif check.status != "disabled":
            check.status = "disabled"
            check.next_run_at = None
            disabled += 1

    for spec in specs:
        key = (spec.kind, spec.column_name or "")
        try:
            validated_params = _validate_spec(spec)
        except ValueError as exc:
            skipped.append(
                schemas.MonitorPackSkipped(
                    kind=spec.kind,
                    column_name=spec.column_name,
                    code="invalid_spec",
                    reason=str(exc),
                )
            )
            continue
        check = managed_by_key.get(key)
        if check is None and _non_managed_duplicate(db, dataset.id, spec):
            skipped.append(
                schemas.MonitorPackSkipped(
                    kind=spec.kind,
                    column_name=spec.column_name,
                    code="nonmanaged_check_exists",
                    reason="A non-system check already monitors this dataset/column.",
                )
            )
            continue
        desired_keys.add(key)
        if check is None:
            check = _create_managed_check(db, dataset, pack, spec, actor_id, validated_params)
            managed_by_key[key] = check
            created += 1
            continue
        if _update_managed_check(check, pack, spec, validated_params):
            updated += 1

    for check in managed:
        key = _managed_key(check)
        if key is not None and key not in desired_keys and check.status != "disabled":
            check.status = "disabled"
            check.next_run_at = None
            disabled += 1

    status = "ready" if not skipped else "partial"
    result = schemas.MonitorPackReconciliationOut(
        status=status,
        profile_id=profile.id,
        created=created,
        updated=updated,
        disabled=disabled,
        skipped=skipped,
        message="Monitor pack reconciled.",
    )
    _record_result(pack, result)
    db.flush()
    return monitor_pack_out(db, pack, result)


def monitor_pack_out(
    db: Session,
    pack: models.DatasetMonitorPack,
    reconciliation: schemas.MonitorPackReconciliationOut | None = None,
) -> schemas.MonitorPackOut:
    out = schemas.MonitorPackOut.model_validate(pack)
    out.config = normalize_monitor_pack_config(pack.config)
    out.reconciliation = reconciliation or _stored_result(pack)
    checks = _managed_checks(db, pack.dataset_id)
    out.managed_checks = [_check_out(c) for c in sorted(checks, key=lambda c: c.id)]
    return out


def merge_monitor_pack_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(base, override)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def _record_result(
    pack: models.DatasetMonitorPack,
    result: schemas.MonitorPackReconciliationOut,
    *,
    error: str = "",
) -> None:
    pack.status = result.status
    pack.last_error = error
    pack.last_result = result.model_dump(mode="json")
    pack.last_reconciled_at = utcnow()
    pack.updated_at = utcnow()


def _stored_result(pack: models.DatasetMonitorPack) -> schemas.MonitorPackReconciliationOut | None:
    if not pack.last_result:
        return schemas.MonitorPackReconciliationOut(
            status=pack.status,
            message="Monitor pack has not reconciled yet.",
        )
    return schemas.MonitorPackReconciliationOut.model_validate(pack.last_result)


def _pending_profile_result(config: dict[str, Any]) -> schemas.MonitorPackReconciliationOut:
    skipped = [
        schemas.MonitorPackSkipped(
            kind=kind,
            code="missing_profile",
            reason="Profile the dataset before reconciling monitor checks.",
        )
        for kind in MONITOR_KINDS
        if _monitor_enabled(config, kind)
    ]
    return schemas.MonitorPackReconciliationOut(
        status="pending_profile",
        skipped=skipped,
        message="Profile the dataset before reconciling monitor checks.",
    )


def _build_monitor_specs(
    dataset: models.Dataset,
    profile: models.Profile,
    config: dict[str, Any],
) -> tuple[list[MonitorSpec], list[schemas.MonitorPackSkipped]]:
    specs: list[MonitorSpec] = []
    skipped: list[schemas.MonitorPackSkipped] = []

    if _monitor_enabled(config, "freshness"):
        spec = _freshness_spec(dataset, profile, config)
        if spec:
            specs.append(spec)
        else:
            skipped.append(
                schemas.MonitorPackSkipped(
                    kind="freshness",
                    code="no_temporal_column",
                    reason="No profiled temporal column is available for freshness monitoring.",
                )
            )

    if _monitor_enabled(config, "volume"):
        specs.append(_volume_spec(dataset, config))

    if _monitor_enabled(config, "schema"):
        spec, reason = _schema_spec(dataset, profile, config)
        if spec:
            specs.append(spec)
        else:
            skipped.append(reason)

    if _monitor_enabled(config, "drift"):
        drift_specs = _drift_specs(dataset, profile, config)
        if drift_specs:
            specs.extend(drift_specs)
        else:
            skipped.append(
                schemas.MonitorPackSkipped(
                    kind="drift",
                    code="no_drift_columns",
                    reason="No profiled numeric or low-cardinality categorical columns are suitable for drift checks.",
                )
            )

    return specs, skipped


def _monitor_enabled(config: dict[str, Any], kind: str) -> bool:
    return bool((config.get("monitors") or {}).get(kind, True))


def _schedule_minutes(config: dict[str, Any], kind: str) -> int:
    cadence = config.get("cadence") or {}
    default = (DEFAULT_MONITOR_PACK_CONFIG["cadence"] or {}).get(f"{kind}_minutes", 1440)
    raw = cadence.get(f"{kind}_minutes", default)
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = int(default)
    return max(1, minutes)


def _kind_override(config: dict[str, Any], kind: str) -> dict[str, Any]:
    overrides = config.get("overrides") or {}
    direct = config.get(kind) or {}
    return _deep_merge(overrides.get(kind) or {}, direct if isinstance(direct, dict) else {})


def _freshness_spec(
    dataset: models.Dataset,
    profile: models.Profile,
    config: dict[str, Any],
) -> MonitorSpec | None:
    temporal = list((profile.table_facts or {}).get("temporal_columns") or [])
    if not temporal:
        return None
    preferred = _best_temporal_column(temporal)
    col = preferred["name"]
    override = _kind_override(config, "freshness")
    max_age = override.get("max_age_hours")
    if max_age is None and dataset.knowledge and dataset.knowledge.freshness_sla_hours:
        max_age = dataset.knowledge.freshness_sla_hours
    if max_age is None:
        max_age = (config.get("sensitivity") or {}).get("freshness_max_age_hours", 48)
    return MonitorSpec(
        kind="freshness",
        check_type="freshness",
        column_name=col,
        params={"max_age_hours": max_age},
        severity="error" if dataset.knowledge and dataset.knowledge.freshness_sla_hours else "warn",
        name=f"{dataset.table_name}: freshness monitor on {col}",
        rationale="System monitor pack freshness check.",
        schedule_minutes=_schedule_minutes(config, "freshness"),
    )


def _best_temporal_column(temporal_columns: list[dict[str, Any]]) -> dict[str, Any]:
    hints = ("updated", "modified", "event", "created", "date", "time", "_at", "ts")

    def score(col: dict[str, Any]) -> tuple[int, str]:
        name = str(col.get("name") or "").lower()
        hit = next((len(hints) - i for i, h in enumerate(hints) if h in name), 0)
        return (hit, name)

    return sorted(temporal_columns, key=score, reverse=True)[0]


def _volume_spec(dataset: models.Dataset, config: dict[str, Any]) -> MonitorSpec:
    sensitivity = config.get("sensitivity") or {}
    override = _kind_override(config, "volume")
    params = {
        "sigma": override.get("sigma", sensitivity.get("volume_sigma", 3.0)),
        "lookback_runs": override.get("lookback_runs", sensitivity.get("volume_lookback_runs", 14)),
        "min_history": override.get("min_history", sensitivity.get("volume_min_history", 5)),
    }
    return MonitorSpec(
        kind="volume",
        check_type="row_count_anomaly",
        column_name=None,
        params=params,
        severity=str(override.get("severity", "warn")),
        name=f"{dataset.table_name}: volume monitor",
        rationale="System monitor pack row-count anomaly check.",
        schedule_minutes=_schedule_minutes(config, "volume"),
    )


def _schema_spec(
    dataset: models.Dataset,
    profile: models.Profile,
    config: dict[str, Any],
) -> tuple[MonitorSpec | None, schemas.MonitorPackSkipped]:
    if "schema_contract" not in CHECK_TYPES:
        return None, schemas.MonitorPackSkipped(
            kind="schema",
            code="check_type_unavailable",
            reason="schema_contract check type is not available in this build.",
        )
    override = _kind_override(config, "schema")
    params = _schema_contract_params(profile)
    params.update(override.get("params") or {})
    return MonitorSpec(
        kind="schema",
        check_type="schema_contract",
        column_name=None,
        params=params,
        severity=str(override.get("severity", "warn")),
        name=f"{dataset.table_name}: schema contract monitor",
        rationale="System monitor pack schema contract check.",
        schedule_minutes=_schedule_minutes(config, "schema"),
    ), schemas.MonitorPackSkipped(kind="schema", reason="")


def _schema_contract_params(profile: models.Profile) -> dict[str, Any]:
    ct = CHECK_TYPES["schema_contract"]
    columns = [
        {"name": c.get("name"), "dtype": c.get("dtype"), "kind": c.get("kind")}
        for c in (profile.columns or [])
    ]
    params: dict[str, Any] = {}
    for p in ct.params:
        name = p["name"]
        if name in {"columns", "expected_columns"}:
            params[name] = columns
        elif name in {"schema", "contract"}:
            params[name] = {"columns": columns}
        elif name in {"profile_id", "baseline_profile_id"}:
            params[name] = profile.id
        elif p.get("default") is not None:
            params[name] = p["default"]
    return params


def _drift_specs(
    dataset: models.Dataset,
    profile: models.Profile,
    config: dict[str, Any],
) -> list[MonitorSpec]:
    override = _kind_override(config, "drift")
    sensitivity = config.get("sensitivity") or {}
    threshold = override.get("threshold", sensitivity.get("drift_threshold", 0.2))
    max_checks = int((config.get("limits") or {}).get("max_drift_checks", 4))
    max_checks = max(0, max_checks)
    if not max_checks:
        return []
    columns = list(profile.columns or [])
    pk_candidates = {str(c).lower() for c in (profile.table_facts or {}).get("pk_candidates", [])}

    numeric: list[tuple[float, str]] = []
    for col in columns:
        name = str(col.get("name") or "")
        lname = name.lower()
        if col.get("kind") != "numeric" or lname in pk_candidates or any(h == lname for h in ID_HINTS):
            continue
        if not col.get("quantiles"):
            continue
        stddev = col.get("stddev")
        if isinstance(stddev, (int, float)) and stddev:
            numeric.append((abs(float(stddev)), name))

    categorical: list[tuple[int, str]] = []
    for col in columns:
        name = str(col.get("name") or "")
        lname = name.lower()
        if col.get("kind") != "string" or any(h in lname for h in ID_HINTS):
            continue
        distinct = int(col.get("distinct_count") or 0)
        if 1 < distinct <= 20 and col.get("top_values"):
            categorical.append((distinct, name))

    selected = [name for _, name in sorted(numeric, reverse=True)[:max_checks]]
    remaining = max_checks - len(selected)
    selected.extend(name for _, name in sorted(categorical)[:remaining])

    specs: list[MonitorSpec] = []
    for name in selected:
        specs.append(
            MonitorSpec(
                kind="drift",
                check_type="distribution_drift",
                column_name=name,
                params={"method": "psi", "threshold": threshold},
                severity=str(override.get("severity", "info")),
                name=f"{dataset.table_name}: drift monitor on {name}",
                rationale="System monitor pack distribution drift check.",
                schedule_minutes=_schedule_minutes(config, "drift"),
            )
        )
    return specs


def _managed_checks(db: Session, dataset_id: int) -> list[models.Check]:
    checks = (
        db.query(models.Check)
        .filter(models.Check.dataset_id == dataset_id, models.Check.status != "archived")
        .all()
    )
    return [c for c in checks if _is_monitor_pack_check(c)]


def _is_monitor_pack_check(check: models.Check) -> bool:
    meta = (check.params or {}).get("monitor_pack") or {}
    return bool(check.origin == "system" and meta.get("managed") is True and meta.get("kind"))


def _managed_key(check: models.Check) -> tuple[str, str] | None:
    meta = (check.params or {}).get("monitor_pack") or {}
    kind = meta.get("kind")
    if not kind:
        return None
    return str(kind), check.column_name or ""


def _non_managed_duplicate(db: Session, dataset_id: int, spec: MonitorSpec) -> bool:
    checks = (
        db.query(models.Check)
        .filter(
            models.Check.dataset_id == dataset_id,
            models.Check.status != "archived",
            models.Check.check_type == spec.check_type,
        )
        .all()
    )
    for check in checks:
        if check.column_name == spec.column_name and not _is_monitor_pack_check(check):
            return True
    return False


def _create_managed_check(
    db: Session,
    dataset: models.Dataset,
    pack: models.DatasetMonitorPack,
    spec: MonitorSpec,
    actor_id: int | None,
    validated_params: dict[str, Any],
) -> models.Check:
    params = _params_with_identity(pack, spec, validated_params)
    check = models.Check(
        dataset_id=dataset.id,
        name=spec.name,
        check_type=spec.check_type,
        column_name=spec.column_name,
        params=params,
        severity=spec.severity,
        status="active",
        origin="system",
        rationale=spec.rationale,
        schedule_kind="interval",
        schedule_expr=str(spec.schedule_minutes),
        next_run_at=utcnow(),
        created_by_id=actor_id,
    )
    db.add(check)
    db.flush()
    return check


def _update_managed_check(
    check: models.Check,
    pack: models.DatasetMonitorPack,
    spec: MonitorSpec,
    validated_params: dict[str, Any],
) -> bool:
    params = _params_with_identity(pack, spec, validated_params)
    changed = False
    for field, value in (
        ("name", spec.name),
        ("check_type", spec.check_type),
        ("column_name", spec.column_name),
        ("params", params),
        ("severity", spec.severity),
        ("rationale", spec.rationale),
        ("schedule_kind", "interval"),
        ("schedule_expr", str(spec.schedule_minutes)),
    ):
        if getattr(check, field) != value:
            setattr(check, field, value)
            changed = True
    if check.status != "active":
        check.status = "active"
        check.next_run_at = utcnow()
        changed = True
    elif changed and check.next_run_at is None:
        check.next_run_at = utcnow()
    return changed


def _validate_spec(spec: MonitorSpec) -> dict[str, Any]:
    return validate_check(spec.check_type, spec.column_name, spec.params)


def _params_with_identity(
    pack: models.DatasetMonitorPack,
    spec: MonitorSpec,
    params: dict[str, Any],
) -> dict[str, Any]:
    return {
        **params,
        "monitor_pack": {
            "version": PACK_VERSION,
            "kind": spec.kind,
            "pack_id": pack.id,
            "managed": True,
        },
    }


def _disable_managed_checks(db: Session, dataset_id: int) -> int:
    disabled = 0
    for check in _managed_checks(db, dataset_id):
        if check.status != "disabled":
            check.status = "disabled"
            check.next_run_at = None
            disabled += 1
    return disabled


def _check_out(check: models.Check) -> schemas.CheckOut:
    out = schemas.CheckOut.model_validate(check)
    out.dataset_name = check.dataset.table_name if check.dataset else ""
    return out
