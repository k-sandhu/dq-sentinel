"""Shared ORM -> schema serializers that need joined display fields."""

from sqlalchemy.orm import Session

from app import models, schemas


def mask_dsn(dsn: str) -> str:
    """Hide credentials in a DSN for display."""
    if "@" in dsn and "://" in dsn:
        scheme, rest = dsn.split("://", 1)
        if "@" in rest:
            creds, host = rest.rsplit("@", 1)
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:****@{host}"
    return dsn


def connection_out(conn: models.Connection, dataset_count: int = 0) -> schemas.ConnectionOut:
    out = schemas.ConnectionOut.model_validate(conn)
    out.dsn_masked = mask_dsn(conn.dsn)
    out.dataset_count = dataset_count
    return out


def check_out(check: models.Check, dataset_name: str | None = None) -> schemas.CheckOut:
    out = schemas.CheckOut.model_validate(check)
    out.dataset_name = dataset_name or (check.dataset.table_name if check.dataset else "")
    return out


def run_out(db: Session, run: models.CheckRun) -> schemas.RunOut:
    out = schemas.RunOut.model_validate(run)
    check = run.check
    if check:
        out.check_name = check.name
        out.check_type = check.check_type
        out.dataset_name = check.dataset.table_name if check.dataset else ""
    out.exception_count = (
        db.query(models.ExceptionRecord).filter(models.ExceptionRecord.run_id == run.id).count()
    )
    return out


def _display_name(user: models.User | None) -> str | None:
    """Display name (fall back to email when name is empty), '(inactive)' suffix
    for deactivated users so reassignment need is visible (#56)."""
    if user is None:
        return None
    name = user.name or user.email
    return f"{name} (inactive)" if not user.is_active else name


def exception_out(db: Session, exc: models.ExceptionRecord) -> schemas.ExceptionOut:
    out = schemas.ExceptionOut.model_validate(exc)
    check = db.get(models.Check, exc.check_id)
    if check:
        out.check_name = check.name
        out.check_type = check.check_type
        out.check_severity = check.severity
        out.column_name = check.column_name
    dataset = db.get(models.Dataset, exc.dataset_id)
    if dataset:
        out.dataset_name = dataset.table_name
    if exc.marked_by_id:
        user = db.get(models.User, exc.marked_by_id)
        out.marked_by = user.name or user.email if user else None
    if exc.assigned_to_id:
        out.assigned_to = _display_name(db.get(models.User, exc.assigned_to_id))
    return out


def exception_event_out(db: Session, ev: models.ExceptionEvent) -> schemas.ExceptionEventOut:
    out = schemas.ExceptionEventOut.model_validate(ev)
    # Surface display names only (no emails in event payloads — privacy, #56).
    out.user = _display_name(db.get(models.User, ev.user_id)) if ev.user_id else None
    return out


def custom_dashboard_meta(
    db: Session, d: models.CustomDashboard, owner: models.User | None = None
) -> schemas.CustomDashboardMeta:
    """Meta (no layout) with the owner's display name + active flag. Pass `owner`
    to avoid a per-row lookup when listing."""
    out = schemas.CustomDashboardMeta.model_validate(d)
    if owner is None:
        owner = db.get(models.User, d.owner_id)
    if owner is not None:
        out.owner_name = owner.name or owner.email
        out.owner_active = owner.is_active
    else:  # owner row hard-deleted — keep the dashboard usable, label it honestly
        out.owner_name = "(unknown)"
        out.owner_active = False
    out.widget_count = len((d.layout or {}).get("widgets", []))
    return out


def dataset_out(db: Session, ds: models.Dataset) -> schemas.DatasetOut:
    out = schemas.DatasetOut.model_validate(ds)
    out.connection_name = ds.connection.name if ds.connection else ""
    active = [c for c in ds.checks if c.status == "active"]
    out.active_checks = len(active)
    out.open_exceptions = (
        db.query(models.ExceptionRecord)
        .filter(models.ExceptionRecord.dataset_id == ds.id, models.ExceptionRecord.status == "open")
        .count()
    )
    statuses = {c.last_status for c in active if c.last_status}
    if not statuses:
        out.health = "unknown"
    elif "fail" in statuses or "error" in statuses:
        out.health = "fail"
    elif "warn" in statuses:
        out.health = "warn"
    else:
        out.health = "pass"
    if ds.knowledge:
        out.importance = ds.knowledge.importance
        out.owner = ds.knowledge.owner or None
        out.domain = ds.knowledge.domain or None
        out.team = ds.knowledge.team or None
        out.slo_target_score = ds.knowledge.slo_target_score
        out.slo_window_days = ds.knowledge.slo_window_days
        out.slo_enabled = ds.knowledge.slo_enabled
    return out
