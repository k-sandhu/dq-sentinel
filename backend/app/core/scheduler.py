"""Worker loop: claim due checks and run them.

Claiming uses an optimistic compare-and-swap UPDATE on next_run_at, so multiple
workers can poll the same database without double-running a check (on PostgreSQL;
run a single worker against SQLite). See issue #25 for the queue-based design.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import update

from app.config import get_settings
from app.core.runner import compute_next_run, run_check
from app.db import session_factory
from app.models import Check, utcnow

log = logging.getLogger(__name__)


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
                executor.submit(_execute, check.id)
    return claimed


def run_forever() -> None:  # pragma: no cover - long-running entrypoint
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info(
        "DQ Sentinel worker started (poll every %ss, concurrency %s)",
        settings.worker_poll_seconds, settings.worker_concurrency,
    )
    with ThreadPoolExecutor(max_workers=settings.worker_concurrency) as executor:
        while True:
            try:
                n = poll_once(executor)
                if n:
                    log.info("Claimed %s due check(s)", n)
            except Exception:  # noqa: BLE001 - the loop must survive transient DB errors
                log.exception("Scheduler pass failed; continuing")
            time.sleep(settings.worker_poll_seconds)
