"""Worker loop: claim due checks and run them.

Claiming uses an optimistic compare-and-swap UPDATE on next_run_at, so multiple
workers can poll the same database without double-running a check (on PostgreSQL;
run a single worker against SQLite). See issue #25 for the queue-based design.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import delete, update

from app.config import get_settings
from app.core.incidents import process_due_escalations
from app.core.runner import compute_next_run, run_check
from app.db import session_factory
from app.models import AuditEntry, Check, utcnow
from app.observability import WORKER_CLAIMS, WORKER_UP

log = logging.getLogger(__name__)

# Last time the audit-retention purge / SLA evaluation ran (process-local; the
# worker is the only long-lived process that calls poll_once on a loop).
_last_audit_purge: datetime | None = None
_last_sla_eval: datetime | None = None


def maybe_evaluate_slas(now: datetime | None = None) -> int:
    """Recompute SLA rollups (#102), self-throttled to ``sla_eval_seconds``.

    Returns the number of SLAs evaluated (0 when throttled). Runs in the worker
    loop alongside check scheduling; failures are swallowed by poll_once's guard.
    """
    global _last_sla_eval
    now = now or utcnow()
    interval = timedelta(seconds=max(30, get_settings().sla_eval_seconds))
    if _last_sla_eval is not None and now - _last_sla_eval < interval:
        return 0
    _last_sla_eval = now
    from app.core.sla import evaluate_all

    factory = session_factory()
    with factory() as db:
        return len(evaluate_all(db, now))


def purge_audit_log(now: datetime | None = None) -> int:
    """Delete audit rows older than ``audit_retention_days``. Runs at most once
    per 24h (issue #30). Returns rows deleted. ``audit_retention_days <= 0``
    disables purging (keep everything).
    """
    global _last_audit_purge
    now = now or utcnow()
    if _last_audit_purge is not None and now - _last_audit_purge < timedelta(days=1):
        return 0
    _last_audit_purge = now
    retention = get_settings().audit_retention_days
    if retention <= 0:
        return 0
    cutoff = now - timedelta(days=retention)
    factory = session_factory()
    with factory() as db:
        res = db.execute(delete(AuditEntry).where(AuditEntry.created_at < cutoff))
        db.commit()
        deleted = res.rowcount or 0
    if deleted:
        log.info("Purged %s audit row(s) older than %s days", deleted, retention)
    return deleted


def _execute(check_id: int) -> None:
    factory = session_factory()
    with factory() as db:
        check = db.get(Check, check_id)
        if check is None or check.status != "active":
            return
        run = run_check(db, check, triggered_by="schedule")
        log.info(
            "Ran check %s '%s' -> %s (%s violations)",
            check.id, check.name, run.status, run.violation_count,
        )


def poll_once(executor: ThreadPoolExecutor) -> int:
    """One scheduling pass. Returns number of checks claimed."""
    factory = session_factory()
    now = utcnow()
    purge_audit_log(now)  # self-throttles to at most once per day
    try:
        maybe_evaluate_slas(now)  # self-throttles to sla_eval_seconds
    except Exception:  # noqa: BLE001 - SLA evaluation must never block check scheduling
        log.exception("SLA evaluation pass failed; continuing")
    try:
        factory = session_factory()
        with factory() as db:
            process_due_escalations(db, now)
    except Exception:  # noqa: BLE001 - escalation sends must never block check scheduling
        log.exception("Incident escalation pass failed; continuing")
    claimed = 0
    with factory() as db:
        # Initialize schedules that were activated without a next_run_at
        missing = (
            db.query(Check)
            .filter(Check.status == "active", Check.next_run_at.is_(None), Check.schedule_expr.isnot(None))
            .all()
        )
        for c in missing:
            c.next_run_at = now
        if missing:
            db.commit()

        due = (
            db.query(Check)
            .filter(Check.status == "active", Check.next_run_at.isnot(None), Check.next_run_at <= now)
            .order_by(Check.next_run_at)
            .limit(20)
            .all()
        )
        for check in due:
            nxt = compute_next_run(check, now)
            res = db.execute(
                update(Check)
                .where(Check.id == check.id, Check.next_run_at == check.next_run_at)
                .values(next_run_at=nxt)
            )
            db.commit()
            if res.rowcount == 1:  # we won the claim
                claimed += 1
                WORKER_CLAIMS.inc()
                executor.submit(_execute, check.id)
    return claimed


def run_forever() -> None:  # pragma: no cover - long-running entrypoint
    settings = get_settings()
    log.info(
        "DQ Sentinel worker started (poll every %ss, concurrency %s)",
        settings.worker_poll_seconds, settings.worker_concurrency,
    )
    WORKER_UP.set(1)
    try:
        with ThreadPoolExecutor(max_workers=settings.worker_concurrency) as executor:
            while True:
                try:
                    n = poll_once(executor)
                    if n:
                        log.info("Claimed %s due check(s)", n)
                except Exception:  # noqa: BLE001 - the loop must survive transient DB errors
                    log.exception("Scheduler pass failed; continuing")
                time.sleep(settings.worker_poll_seconds)
    finally:
        WORKER_UP.set(0)
