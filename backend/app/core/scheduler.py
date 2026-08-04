"""Worker loop: claim due checks and run them.

Claiming uses an optimistic compare-and-swap UPDATE on next_run_at, so multiple
workers can poll the same database without double-running a check (on PostgreSQL;
run a single worker against SQLite). See issue #25 for the queue-based design.

The API's manual-run path goes through the same CAS (``claim_due_slot``): a run
started by hand consumes the slot it would otherwise have raced (#257).
"""

import logging
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

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
_last_scorecard_snapshot: date | None = None

# Cooperative shutdown (#157). SIGTERM/SIGINT set this event; run_forever then
# breaks its loop and drains in-flight checks (the ThreadPoolExecutor __exit__
# waits for submitted runs) instead of the process being killed mid-run.
_STOP = threading.Event()


def request_stop(_signum: int | None = None, _frame: object | None = None) -> None:
    """Signal the worker loop to stop after the current pass and drain."""
    _STOP.set()


def _install_signal_handlers() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, request_stop)
        except ValueError:  # not the main thread (e.g. tests) — caller drives _STOP
            log.debug("Could not install handler for %s (not main thread)", sig)


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


def maybe_capture_scorecard_snapshots(now: datetime | None = None) -> int:
    """Capture aggregate scorecard history once per UTC day (#119)."""
    global _last_scorecard_snapshot
    now = now or utcnow()
    today = now.date()
    if _last_scorecard_snapshot == today:
        return 0
    from app.core.scorecards import capture_scorecard_snapshots

    factory = session_factory()
    with factory() as db:
        snapshots = capture_scorecard_snapshots(db, today)
        db.commit()
    _last_scorecard_snapshot = today
    return len(snapshots)


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


def _cas_next_run(db: Session, check: Check, new: datetime | None) -> bool:
    """Optimistic compare-and-swap on ``next_run_at``: move the slot only if it is
    still the value we read. Whoever gets rowcount 1 owns that slot; everyone else
    backs off. Commits, so the claim is durable before the check is executed.
    """
    expected = check.next_run_at
    res = db.execute(
        update(Check)
        .where(Check.id == check.id, Check.next_run_at == expected)
        .values(next_run_at=new)
    )
    db.commit()
    return res.rowcount == 1


def claim_due_slot(db: Session, check: Check, now: datetime | None = None) -> bool:
    """Consume an already-due scheduled slot ahead of an out-of-band run (#257).

    A manual run and the worker are two doors onto the same check. A newly created
    active check is due immediately (``next_run_at = utcnow()``), so clicking Run
    produced the run the analyst asked for *and* left the slot standing: the next
    poll claimed it seconds later, and the analyst got two identical failures in
    the same minute plus x2 recurrence on rows nobody had triaged yet.

    So the manual path claims that slot first, through the SAME CAS the worker
    uses — a worker that read the same slot simply loses the swap and moves on
    instead of double-running (multi-worker safe, and the single-worker default
    can no longer race its own poll interval).

    Deliberately narrow, in both directions:

    * Only a slot that is **already due** is consumed. A future slot is left
      exactly where it is, so running manually at 09:00 never postpones the 10:00
      daily slot the analyst is relying on.
    * Nothing is skipped: the slot is consumed by an execution of the check
      happening right now, which is exactly what the worker would have done with
      it. The check keeps its cadence from this run onward (no starvation), and
      this does not move toward the "skip a slot" semantics #165 warns about.

    ``next_run_at IS NULL`` on an active, scheduled check counts as due, because
    that is precisely what ``poll_once`` initializes to ``now`` and claims in the
    same pass.

    Returns True if this caller won the slot. Caller runs the check either way —
    a manual run must always be possible.
    """
    now = now or utcnow()
    if check.status != "active":
        return False  # disabled/proposed/archived checks have no slot to consume
    current = check.next_run_at
    if current is None:
        if not check.schedule_expr:
            return False  # unscheduled: never due, nothing to claim
    elif current > now:
        return False  # a future slot; a manual run must not postpone it
    try:
        nxt = compute_next_run(check, now)
    except Exception:  # noqa: BLE001 - mirrors poll_once: a bad expr must not fail the run
        log.exception(
            "Check %s has an unparseable schedule (%s %r); running without claiming its slot",
            check.id, check.schedule_kind, check.schedule_expr,
        )
        return False
    return _cas_next_run(db, check, nxt)


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
        maybe_capture_scorecard_snapshots(now)  # self-throttles to once per day
    except Exception:  # noqa: BLE001 - scorecard capture must never block check scheduling
        log.exception("Scorecard snapshot capture failed; continuing")
    try:
        with session_factory()() as db:
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
            try:
                nxt = compute_next_run(check, now)
            except Exception:  # noqa: BLE001 - one bad schedule_expr must not wedge the pass
                # Authoring now validates schedule_expr, but a row predating that
                # (or hand-edited in the DB) could still be unparseable. Park it —
                # clear next_run_at so it stops sorting first and blocking siblings —
                # instead of raising and aborting every later check forever.
                log.exception(
                    "Check %s has an unparseable schedule (%s %r); parking it (next_run_at=NULL)",
                    check.id, check.schedule_kind, check.schedule_expr,
                )
                _cas_next_run(db, check, None)
                continue
            if _cas_next_run(db, check, nxt):  # we won the claim
                claimed += 1
                WORKER_CLAIMS.inc()
                executor.submit(_execute, check.id)
    return claimed


def run_forever() -> None:
    settings = get_settings()
    _install_signal_handlers()
    log.info(
        "DQ Sentinel worker started (poll every %ss, concurrency %s)",
        settings.worker_poll_seconds, settings.worker_concurrency,
    )
    WORKER_UP.set(1)
    try:
        with ThreadPoolExecutor(max_workers=settings.worker_concurrency) as executor:
            while not _STOP.is_set():
                try:
                    n = poll_once(executor)
                    if n:
                        log.info("Claimed %s due check(s)", n)
                except Exception:  # noqa: BLE001 - the loop must survive transient DB errors
                    log.exception("Scheduler pass failed; continuing")
                # Interruptible sleep: a stop signal wakes us immediately rather
                # than waiting out a full poll interval.
                _STOP.wait(settings.worker_poll_seconds)
            log.info("Stop requested; draining in-flight checks before exit")
        # Exiting the `with` runs executor.shutdown(wait=True): submitted checks
        # finish (and write their CheckRun rows) instead of being abandoned. The
        # container stop_grace_period bounds the drain before a forced SIGKILL.
        log.info("Worker drained and stopped cleanly")
    finally:
        WORKER_UP.set(0)
