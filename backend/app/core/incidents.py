"""Incident lifecycle, dedupe, recovery, acknowledgement, and escalation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core import notify
from app.models import Check, CheckRun, Incident, IncidentEvent, User, utcnow

log = logging.getLogger(__name__)

FAILURE_STATUSES = {"fail", "error"}
ACTIVE_INCIDENT_STATUSES = {"open", "acknowledged"}


def dedupe_key_for(check: Check) -> str:
    return f"check:{check.id}:failure"


def record_failure(db: Session, check: Check, run: CheckRun, prev_status: str | None) -> Incident:
    """Open or update the check's durable incident for a failed/error run."""
    now = utcnow()
    key = dedupe_key_for(check)
    incident = db.query(Incident).filter(Incident.dedupe_key == key).one_or_none()
    should_notify = False

    if incident is None:
        incident = Incident(
            dataset_id=check.dataset_id,
            check_id=check.id,
            current_run_id=run.id,
            dedupe_key=key,
            title=_title_for(check),
            severity=check.severity,
            status="open",
            failure_status=run.status,
            first_seen_at=now,
            last_seen_at=now,
            occurrence_count=1,
            external_refs={},
        )
        db.add(incident)
        db.flush()
        _event(db, incident, "opened", run_id=run.id, failure_status=run.status)
        should_notify = prev_status not in FAILURE_STATUSES
    else:
        was_resolved = incident.status == "resolved"
        incident.dataset_id = check.dataset_id
        incident.check_id = check.id
        incident.current_run_id = run.id
        incident.title = _title_for(check)
        incident.severity = check.severity
        incident.failure_status = run.status
        incident.last_seen_at = now
        incident.occurrence_count = (incident.occurrence_count or 0) + 1
        if was_resolved:
            incident.status = "open"
            incident.resolved_at = None
            incident.escalation_level = 0
            _event(db, incident, "opened", run_id=run.id, failure_status=run.status, reopened=True)
            should_notify = True
        else:
            _event(
                db,
                incident,
                "occurred",
                run_id=run.id,
                failure_status=run.status,
                occurrence_count=incident.occurrence_count,
            )
            if incident.last_notified_at is None:
                should_notify = True
            elif prev_status in ("pass", "warn", None) and _outside_dedupe_window(db, incident, check, run, now):
                should_notify = True

    if not should_notify and incident.next_escalation_at is None and incident.last_notified_at is not None:
        incident.next_escalation_at = _next_escalation_at(db, incident, check, run, incident.last_notified_at)

    db.commit()
    db.refresh(incident)

    if should_notify:
        _dispatch_best_effort(db, incident, check, run, "triggered", now)
    return incident


def record_recovery(db: Session, check: Check, run: CheckRun, prev_status: str | None) -> Incident | None:
    """Resolve the check's active incident and send one recovery notification."""
    key = dedupe_key_for(check)
    incident = db.query(Incident).filter(Incident.dedupe_key == key).one_or_none()
    if incident is None:
        if prev_status not in FAILURE_STATUSES:
            return None
        now = utcnow()
        incident = Incident(
            dataset_id=check.dataset_id,
            check_id=check.id,
            current_run_id=run.id,
            dedupe_key=key,
            title=_title_for(check),
            severity=check.severity,
            status="resolved",
            failure_status=prev_status or "fail",
            first_seen_at=now,
            last_seen_at=now,
            resolved_at=now,
            occurrence_count=1,
            external_refs={},
        )
        db.add(incident)
        db.flush()
        _event(db, incident, "recovered", run_id=run.id, previous_status=prev_status, legacy=True)
        db.commit()
        db.refresh(incident)
        _dispatch_best_effort(db, incident, check, run, "recovered", now)
        return incident
    if incident.status == "resolved":
        return incident
    now = utcnow()
    incident.current_run_id = run.id
    incident.last_seen_at = now
    incident.status = "resolved"
    incident.resolved_at = now
    incident.next_escalation_at = None
    _event(db, incident, "recovered", run_id=run.id, previous_status=prev_status)
    db.commit()
    db.refresh(incident)
    _dispatch_best_effort(db, incident, check, run, "recovered", now)
    return incident


def acknowledge_incident(db: Session, incident: Incident, user: User, note: str = "") -> Incident:
    if incident.status == "resolved":
        return incident
    if incident.status != "acknowledged":
        incident.status = "acknowledged"
        _event(db, incident, "acknowledged", user_id=user.id, note=note)
    elif note:
        _event(db, incident, "acknowledged", user_id=user.id, note=note, repeated=True)
    db.commit()
    db.refresh(incident)
    return incident


def resolve_incident(db: Session, incident: Incident, user: User, note: str = "") -> Incident:
    if incident.status != "resolved":
        incident.status = "resolved"
        incident.resolved_at = utcnow()
        incident.next_escalation_at = None
        _event(db, incident, "resolved", user_id=user.id, note=note)
    elif note:
        _event(db, incident, "resolved", user_id=user.id, note=note, repeated=True)
    db.commit()
    db.refresh(incident)
    return incident


def process_due_escalations(db: Session, now: datetime | None = None) -> int:
    """Send due escalations and advance each incident's escalation state."""
    now = now or utcnow()
    incidents = (
        db.query(Incident)
        .filter(
            Incident.status.in_(tuple(ACTIVE_INCIDENT_STATUSES)),
            Incident.next_escalation_at.isnot(None),
            Incident.next_escalation_at <= now,
        )
        .order_by(Incident.next_escalation_at, Incident.id)
        .limit(50)
        .all()
    )
    processed = 0
    for incident in incidents:
        check = db.get(Check, incident.check_id)
        run = db.get(CheckRun, incident.current_run_id) if incident.current_run_id else None
        if check is None or run is None:
            incident.next_escalation_at = None
            _event(db, incident, "system", reason="missing check/run for escalation")
            db.commit()
            continue
        max_level = _max_escalation_level(db, check, run)
        if max_level <= incident.escalation_level:
            incident.next_escalation_at = None
            db.commit()
            continue

        old_level = incident.escalation_level
        incident.escalation_level = old_level + 1
        db.commit()
        db.refresh(incident)
        attempted = _dispatch_best_effort(db, incident, check, run, "escalated", now)
        if attempted:
            incident.next_escalation_at = (
                _next_escalation_at(db, incident, check, run, now)
                if incident.escalation_level < max_level
                else None
            )
            _event(
                db,
                incident,
                "escalated",
                run_id=run.id,
                escalation_level=incident.escalation_level,
            )
            db.commit()
            db.refresh(incident)
            processed += 1
        else:
            incident.escalation_level = old_level
            incident.next_escalation_at = None
            _event(db, incident, "system", reason="escalation skipped: no deliverable channels")
            db.commit()
            db.refresh(incident)
    return processed


def _title_for(check: Check) -> str:
    return f"{check.name} failing"


def _event(
    db: Session,
    incident: Incident,
    kind: str,
    *,
    user_id: int | None = None,
    **detail: Any,
) -> IncidentEvent:
    ev = IncidentEvent(
        incident_id=incident.id,
        user_id=user_id,
        kind=kind,
        detail=_safe_detail(detail),
    )
    db.add(ev)
    return ev


def _safe_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Keep timeline details free of source rows and secrets."""
    return {k: v for k, v in detail.items() if v not in (None, "")}


def _outside_dedupe_window(
    db: Session, incident: Incident, check: Check, run: CheckRun, now: datetime
) -> bool:
    if incident.last_notified_at is None:
        return True
    window = min(
        (r.dedupe_window_minutes or 60 for r in notify.incident_rules(db, check, run)),
        default=60,
    )
    return now - incident.last_notified_at >= timedelta(minutes=max(1, window))


def _max_escalation_level(db: Session, check: Check, run: CheckRun) -> int:
    return max((r.max_escalation_level or 0 for r in notify.incident_rules(db, check, run)), default=0)


def _next_escalation_at(
    db: Session, incident: Incident, check: Check, run: CheckRun, base: datetime
) -> datetime | None:
    rules = [
        r
        for r in notify.incident_rules(db, check, run)
        if (r.max_escalation_level or 0) > incident.escalation_level
        and (r.escalation_delay_minutes or 0) > 0
    ]
    if not rules:
        return None
    delay = min(int(r.escalation_delay_minutes or 0) for r in rules)
    return base + timedelta(minutes=max(1, delay))


def _dispatch_best_effort(
    db: Session,
    incident: Incident,
    check: Check,
    run: CheckRun,
    action: str,
    notified_at: datetime,
) -> int:
    try:
        attempted = notify.dispatch_incident(db, incident, check, run, action)
        if attempted:
            incident.last_notified_at = notified_at
            if action == "triggered":
                incident.next_escalation_at = _next_escalation_at(db, incident, check, run, notified_at)
            elif action == "recovered":
                incident.next_escalation_at = None
            _event(db, incident, "notified", action=action, run_id=run.id, attempted=attempted)
        db.commit()
        db.refresh(incident)
        return attempted
    except Exception:  # noqa: BLE001 - notifications must never fail incident persistence
        db.rollback()
        log.warning(
            "incident notification dispatch failed",
            extra={"event": "incident_notify_failed", "incident_id": incident.id},
            exc_info=True,
        )
        return 0
