"""Single audited + versioned path for creating, editing, and restoring checks (#185).

Both the REST API (``api/checks.py``) and the assistant (``llm/chat_agent.py``)
call these helpers so every mutation is validated, audit-logged, AND snapshotted
into ``check_versions`` for rollback. Helpers STAGE rows on the caller's session
(``db.flush()`` only) and never commit — the caller owns the transaction, exactly
like ``core.audit.audit()``.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.check_types import validate_check
from app.models import Check, CheckVersion, Dataset, User, utcnow

# The fields a CheckVersion snapshots — i.e. the check *definition*. Lifecycle
# fields (status, next_run_at, last_*) are deliberately excluded: pausing or
# archiving a check is not a definition change and must not spawn a version.
DEFINITION_FIELDS = (
    "name",
    "column_name",
    "params",
    "severity",
    "rationale",
    "schedule_kind",
    "schedule_expr",
)

# Allowed lifecycle/config enum values. The REST layer enforces these via Pydantic
# Literals, but the assistant calls this shared path directly, so validate here too:
# a bad status/schedule_kind would otherwise create a check the scheduler silently
# never runs. Keep in sync with schemas.Severity / CheckStatus / schedule_kind.
_SEVERITIES = ("info", "warn", "error")
_STATUSES = ("proposed", "active", "disabled", "archived")
_SCHEDULE_KINDS = ("interval", "cron")


def _validate_enum(field: str, value: Any, allowed: tuple[str, ...], *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if value not in allowed:
        suffix = " or null" if allow_none else ""
        raise ValueError(f"{field} must be one of {list(allowed)}{suffix}, got {value!r}")


def _definition_snapshot(check: Check) -> dict[str, Any]:
    snap = {f: getattr(check, f) for f in DEFINITION_FIELDS}
    snap["params"] = copy.deepcopy(snap.get("params") or {})
    return snap


def snapshot_version(db: Session, check: Check, user: User | None, change_note: str) -> CheckVersion:
    """Append the check's current definition as the next version. Caller commits."""
    last = (
        db.query(func.max(CheckVersion.version))
        .filter(CheckVersion.check_id == check.id)
        .scalar()
    )
    cv = CheckVersion(
        check_id=check.id,
        version=int(last or 0) + 1,
        name=check.name,
        check_type=check.check_type,
        column_name=check.column_name,
        params=copy.deepcopy(check.params or {}),
        severity=check.severity,
        rationale=check.rationale or "",
        schedule_kind=check.schedule_kind,
        schedule_expr=check.schedule_expr,
        change_note=change_note[:255],
        created_by_id=getattr(user, "id", None),
    )
    db.add(cv)
    db.flush()
    return cv


def default_check_name(ds: Dataset, check_type: str, column_name: str | None) -> str:
    return f"{ds.table_name}: {check_type}" + (f" on {column_name}" if column_name else "")


def create_check(
    db: Session,
    user: User | None,
    ds: Dataset,
    *,
    name: str = "",
    check_type: str,
    column_name: str | None = None,
    params: dict[str, Any] | None = None,
    severity: str = "error",
    rationale: str = "",
    schedule_kind: str | None = "interval",
    schedule_expr: str | None = "1440",
    status: str = "active",
    origin: str = "manual",
    change_note: str = "created",
) -> Check:
    """Validate, create, audit, and snapshot v1. Raises ValueError on bad params.
    Caller commits + refreshes."""
    _validate_enum("severity", severity, _SEVERITIES)
    _validate_enum("status", status, _STATUSES)
    _validate_enum("schedule_kind", schedule_kind, _SCHEDULE_KINDS, allow_none=True)
    normalized = validate_check(check_type, column_name, params or {})
    check = Check(
        dataset_id=ds.id,
        name=name or default_check_name(ds, check_type, column_name),
        check_type=check_type,
        column_name=column_name,
        params=normalized,
        severity=severity,
        rationale=rationale,
        schedule_kind=schedule_kind,
        schedule_expr=schedule_expr,
        status=status,
        origin=origin,
        created_by_id=getattr(user, "id", None),
        next_run_at=utcnow() if status == "active" else None,
    )
    db.add(check)
    db.flush()  # assign check.id for the audit + version rows
    audit(
        db, user, "check.create", "check", check.id,
        check_type=check.check_type, column=check.column_name, status=check.status,
    )
    snapshot_version(db, check, user, change_note)
    return check


def apply_update(db: Session, user: User | None, check: Check, changes: dict[str, Any]) -> Check:
    """Apply a partial update (``CheckUpdate.model_dump(exclude_unset=True)``),
    re-validate when type-affecting params change, audit, and snapshot a new
    version iff the definition actually changed. Raises ValueError on bad params.
    Caller commits + refreshes."""
    for field, allowed in (("severity", _SEVERITIES), ("status", _STATUSES)):
        if changes.get(field) is not None:
            _validate_enum(field, changes[field], allowed)
    if changes.get("schedule_kind") is not None:
        _validate_enum("schedule_kind", changes["schedule_kind"], _SCHEDULE_KINDS)
    before = _definition_snapshot(check)
    old_params = dict(check.params or {})
    old_status = check.status

    revalidate = (
        ("params" in changes and changes["params"] is not None)
        or ("column_name" in changes and changes["column_name"] != check.column_name)
    )
    if "column_name" in changes:
        check.column_name = changes["column_name"]
    if "params" in changes and changes["params"] is not None:
        check.params = changes["params"]
    if revalidate:
        check.params = validate_check(check.check_type, check.column_name, check.params)
    for field in ("name", "severity", "rationale", "schedule_kind", "schedule_expr"):
        if field in changes and changes[field] is not None:
            setattr(check, field, changes[field])
    if "status" in changes and changes["status"] is not None and changes["status"] != check.status:
        check.status = changes["status"]
        check.next_run_at = utcnow() if check.status == "active" else None

    detail: dict[str, Any] = {"fields": [f for f in changes if changes[f] is not None]}
    if check.params != old_params:
        detail["params_before"] = old_params
        detail["params_after"] = dict(check.params or {})
    if check.status != old_status:
        detail["status"] = {"before": old_status, "after": check.status}
    audit(db, user, "check.update", "check", check.id, **detail)

    if _definition_snapshot(check) != before:
        snapshot_version(db, check, user, changes.get("change_note") or "edited")
    return check


def restore_version(db: Session, user: User | None, check: Check, version: int) -> CheckVersion:
    """Restore the check's definition to a prior version. Re-validates (a column
    or table may have changed shape since), audits, and snapshots the restored
    state as a new version iff it differs from the live definition. Returns the
    source version. Raises LookupError if the version is unknown, or ValueError
    if the restored params no longer validate. Caller commits."""
    cv = (
        db.query(CheckVersion)
        .filter(CheckVersion.check_id == check.id, CheckVersion.version == version)
        .first()
    )
    if cv is None:
        raise LookupError(f"Version {version} not found for check {check.id}")
    before = _definition_snapshot(check)
    # check_type is immutable across a check's life, so a restore never changes it.
    check.name = cv.name
    check.column_name = cv.column_name
    check.params = validate_check(check.check_type, cv.column_name, cv.params or {})
    check.severity = cv.severity
    check.rationale = cv.rationale or ""
    check.schedule_kind = cv.schedule_kind
    check.schedule_expr = cv.schedule_expr
    audit(db, user, "check.restore", "check", check.id, restored_from_version=version)
    if _definition_snapshot(check) != before:
        snapshot_version(db, check, user, f"restored from v{version}")
    return cv
