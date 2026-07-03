"""Admin CRUD for notification routing rules (issue #27).

Rules decide *where* failure/recovery events go; the firing decision is
transition-based and lives in ``core/runner.py``. Admin-gated for writes, any
authenticated user can read. A cheap ``POST /{id}/test`` sends a sample message
through the rule's channel so admins can verify a webhook/SMTP config without
waiting for a real failure."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.core import notify
from app.db import get_db
from app.observability import NOTIFICATIONS_SENT
from app.security import get_current_user, require_role

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _out(db: Session, rule: models.NotificationRule) -> schemas.NotificationRuleOut:
    out = schemas.NotificationRuleOut.model_validate(rule)
    # A Slack rule's target IS its incoming-webhook URL — a bearer credential, same
    # class as webhook/teams. Mask it too, or any viewer could copy it and post to
    # the org channel.
    if rule.channel in {"slack", "webhook", "teams"} and rule.target:
        out.target = _mask_target(rule.target)
    if rule.dataset_id is not None:
        ds = db.get(models.Dataset, rule.dataset_id)
        if ds is not None:
            out.dataset_name = ds.display_name or ds.table_name
    return out


def _validate_dataset(db: Session, dataset_id: int | None) -> None:
    if dataset_id is not None and db.get(models.Dataset, dataset_id) is None:
        raise HTTPException(422, "dataset_id does not exist")


def _mask_target(target: str) -> str:
    if len(target) <= 12:
        return "********"
    return f"{target[:8]}...{target[-4:]}"


def _validate_rule_payload(channel: str, target: str) -> None:
    if channel == "email" and not target.strip():
        raise HTTPException(422, "email rules require at least one recipient in target")


@router.get("/rules", response_model=list[schemas.NotificationRuleOut])
def list_rules(db: Session = Depends(get_db), _: models.User = Depends(get_current_user)):
    rules = db.query(models.NotificationRule).order_by(models.NotificationRule.id).all()
    return [_out(db, r) for r in rules]


@router.post("/rules", response_model=schemas.NotificationRuleOut, status_code=201)
def create_rule(
    body: schemas.NotificationRuleIn,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("admin")),
):
    _validate_dataset(db, body.dataset_id)
    _validate_rule_payload(body.channel, body.target)
    rule = models.NotificationRule(
        dataset_id=body.dataset_id,
        min_severity=body.min_severity,
        channel=body.channel,
        target=body.target,
        on_error_runs=body.on_error_runs,
        dedupe_window_minutes=body.dedupe_window_minutes,
        escalation_delay_minutes=body.escalation_delay_minutes,
        max_escalation_level=body.max_escalation_level,
        enabled=body.enabled,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _out(db, rule)


@router.patch("/rules/{rule_id}", response_model=schemas.NotificationRuleOut)
def update_rule(
    rule_id: int,
    body: schemas.NotificationRuleUpdate,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("admin")),
):
    rule = db.get(models.NotificationRule, rule_id)
    if rule is None:
        raise HTTPException(404, "Notification rule not found")
    data = body.model_dump(exclude_unset=True)
    if "dataset_id" in data:
        _validate_dataset(db, data["dataset_id"])
    new_channel = data["channel"] if data.get("channel") is not None else rule.channel
    new_target = data["target"] if data.get("target") is not None else rule.target
    _validate_rule_payload(new_channel, new_target)
    for field in (
        "dataset_id",
        "min_severity",
        "channel",
        "target",
        "on_error_runs",
        "dedupe_window_minutes",
        "escalation_delay_minutes",
        "max_escalation_level",
        "enabled",
    ):
        # dataset_id and escalation_delay_minutes accept an explicit null: it's how
        # a scoped rule is widened back to "all datasets" / escalation is turned off.
        # The other fields treat null as "leave unchanged".
        if field in ("dataset_id", "escalation_delay_minutes") and field in data:
            setattr(rule, field, data[field])
        elif field in data and data[field] is not None:
            setattr(rule, field, data[field])
    db.commit()
    db.refresh(rule)
    return _out(db, rule)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("admin")),
):
    rule = db.get(models.NotificationRule, rule_id)
    if rule is None:
        raise HTTPException(404, "Notification rule not found")
    db.delete(rule)
    db.commit()


@router.post("/rules/{rule_id}/test")
def test_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("admin")),
):
    """Send a sample message through the rule's channel to verify config."""
    rule = db.get(models.NotificationRule, rule_id)
    if rule is None:
        raise HTTPException(404, "Notification rule not found")
    channel = notify._channel_for(rule)
    if channel is None:
        raise HTTPException(
            422, "Rule has no deliverable target (set target or the matching DQ_* webhook setting)"
        )
    try:
        channel.send(
            "[DQ] Test notification",
            "This is a test from DQ Sentinel. If you can read this, the rule works.",
            None,
        )
        NOTIFICATIONS_SENT.labels(rule.channel, "success").inc()
        return {"ok": True, "message": "Test notification sent"}
    except Exception as exc:  # noqa: BLE001 - surface the reason to the admin testing it
        NOTIFICATIONS_SENT.labels(rule.channel, "failure").inc()
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
