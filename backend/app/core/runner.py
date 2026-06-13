"""Execute checks and persist runs + exception records."""

import hashlib
import json
import logging
import time
from datetime import datetime

from sqlalchemy.orm import Session

from app.connectors.sa import connector_for
from app.core.check_types import CheckContext, CheckResult, run_check_type
from app.core.events import record_event
from app.models import Check, CheckRun, ExceptionEvent, ExceptionRecord, Profile, utcnow
from app.observability import CHECK_RUN_SECONDS, CHECK_RUNS, EXCEPTIONS_RECORDED

log = logging.getLogger(__name__)


def _status_for(check: Check, result: CheckResult) -> str:
    tolerance = int(check.params.get("tolerance", 0) or 0)
    if result.violation_count <= tolerance:
        return "pass"
    return {"info": "warn", "warn": "warn", "error": "fail"}.get(check.severity, "fail")


def exception_fingerprint(check_id: int, row: dict, pk_cols: list[str] | None) -> str:
    """Stable per-row identity for an exception.

    Keying on PK columns keeps identity stable when non-key values drift between
    runs; falls back to the whole sorted row when no PK candidates are known.
    SHA-256 — safe to log/expose (no row contents leak).
    """
    keys = [c for c in (pk_cols or []) if c in row] or sorted(row)
    ident = json.dumps({k: row.get(k) for k in keys}, sort_keys=True, default=str)
    return hashlib.sha256(f"{check_id}|{ident}".encode()).hexdigest()


def _pk_cols_for(db: Session, dataset_id: int) -> list[str] | None:
    """PK candidate columns from the newest profile (one query per run)."""
    profile = (
        db.query(Profile)
        .filter(Profile.dataset_id == dataset_id)
        .order_by(Profile.id.desc())
        .first()
    )
    if profile and isinstance(profile.table_facts, dict):
        cols = profile.table_facts.get("pk_candidates")
        if cols:
            return list(cols)
    return None


def _reconcile_exceptions(
    db: Session, check: Check, run: CheckRun, result: CheckResult, metrics: dict
) -> int:
    """Reconcile violating rows against existing records (identity-based, #55).

    Returns the count of NEWLY inserted records (for the EXCEPTIONS_RECORDED
    counter). Updates `metrics` in place with suppressed/regressed counts.
    """
    pk_cols = _pk_cols_for(db, check.dataset_id)

    # Compute fingerprints for the (capped) sample, then load matches in one query.
    fps: list[str] = [exception_fingerprint(check.id, row, pk_cols) for row in result.sample_rows]
    existing: dict[str, ExceptionRecord] = {}
    if fps:
        rows = (
            db.query(ExceptionRecord)
            .filter(ExceptionRecord.check_id == check.id, ExceptionRecord.fingerprint.in_(fps))
            .all()
        )
        # If duplicate fingerprints exist (legacy/race), keep the newest.
        for rec in rows:
            cur = existing.get(rec.fingerprint)
            if cur is None or rec.id > cur.id:
                existing[rec.fingerprint] = rec

    now = utcnow()
    new_count = 0
    suppressed = 0
    regressed = 0

    for i, row in enumerate(result.sample_rows):
        fp = fps[i]
        reason = result.reasons[i] if i < len(result.reasons) else (result.detail or check.name)
        score = result.scores[i] if i < len(result.scores) else None
        match = existing.get(fp)

        if match is None:
            rec = ExceptionRecord(
                check_id=check.id,
                dataset_id=check.dataset_id,
                run_id=run.id,
                last_run_id=run.id,
                fingerprint=fp,
                row_data=row,
                reason=reason,
                outlier_score=score,
                status="open",
                first_seen_at=now,
                last_seen_at=now,
                occurrence_count=1,
            )
            db.add(rec)
            existing[fp] = rec  # dedupe within this same sample
            new_count += 1
            continue

        # Common bumps for any re-sighting.
        match.last_seen_at = now
        match.occurrence_count = (match.occurrence_count or 0) + 1
        match.last_run_id = run.id
        match.reason = reason
        match.outlier_score = score
        match.row_data = row  # latest snapshot (same data class as before — PII posture unchanged)

        if match.status in ("open", "acknowledged"):
            pass  # bumps only; no status change
        elif match.status in ("expected", "muted"):
            suppressed += 1  # auto-suppress (the missing half of #31)
        elif match.status == "resolved":
            # Regression: reopen, but PRESERVE assigned_to_id + note (prior
            # resolution context is exactly what the analyst needs).
            old = match.status
            match.status = "open"
            regressed += 1
            record_event(
                db, match, "system", from_status=old, to_status="open",
                comment="regressed: row reappeared after resolve",
            )

    if suppressed:
        metrics["suppressed"] = suppressed
    if regressed:
        metrics["regressed"] = regressed
    return new_count


def _auto_resolve_passing(db: Session, check: Check, run: CheckRun) -> None:
    """When a check passes, bulk-resolve its remaining OPEN exceptions (#55).

    Single set-based UPDATE (not a Python loop) — `exception_records` is the
    millions-row table. Only `open` is touched; acknowledged/expected/muted are
    deliberate analyst states and must survive. Always leaves a
    machine-attributable note so a human can tell "I resolved this" from "the
    system did". The `rows_evaluated` guard keeps errored/empty runs from
    resolving anything.
    """
    open_ids = [
        r.id
        for r in db.query(ExceptionRecord.id)
        .filter(ExceptionRecord.check_id == check.id, ExceptionRecord.status == "open")
        .all()
    ]
    if not open_ids:
        return
    now = utcnow()
    db.query(ExceptionRecord).filter(
        ExceptionRecord.check_id == check.id, ExceptionRecord.status == "open"
    ).update(
        {
            ExceptionRecord.status: "resolved",
            ExceptionRecord.note: "auto-resolved: check passing",
            ExceptionRecord.marked_by_id: None,
            ExceptionRecord.marked_at: now,
        },
        synchronize_session=False,
    )
    for exc_id in open_ids:
        db.add(
            ExceptionEvent(
                exception_id=exc_id,
                user_id=None,
                kind="system",
                from_status="open",
                to_status="resolved",
                comment="auto-resolved: check passing",
            )
        )


def run_check(db: Session, check: Check, triggered_by: str = "manual") -> CheckRun:
    dataset = check.dataset
    started = time.perf_counter()
    run = CheckRun(
        check_id=check.id,
        dataset_id=dataset.id,
        started_at=utcnow(),
        triggered_by=triggered_by,
    )
    try:
        connector = connector_for(dataset.connection)
        ctx = CheckContext(
            connector=connector,
            table=dataset.table_name,
            schema=dataset.schema_name,
            column=check.column_name,
            params=check.params or {},
            db=db,
            check_id=check.id,
        )
        result = run_check_type(ctx, check.check_type)
        run.status = _status_for(check, result)
        run.violation_count = result.violation_count
        run.rows_evaluated = result.rows_evaluated
        metrics = dict(result.metrics)
        if result.detail:
            metrics["detail"] = result.detail

        # Flush so the run gets a PK; reconcile then happens inside this same
        # transaction (concurrency: status flips use UPDATE ... WHERE status=...
        # so an overlapping run can double-bump a count but never lose a flip).
        db.add(run)
        db.flush()
        new_records = _reconcile_exceptions(db, check, run, result, metrics)

        # Auto-resolve remaining open exceptions only when the check passes and
        # actually evaluated rows. NEVER auto-resolve on fail runs from a missing
        # fingerprint — sample_rows is capped, a fail run sees only a sample.
        if run.status == "pass" and result.rows_evaluated is not None:
            _auto_resolve_passing(db, check, run)

        run.metrics = metrics
    except Exception as exc:  # noqa: BLE001 - run must record any failure
        log.exception("Check %s (%s) errored", check.id, check.name)
        db.rollback()
        run.status = "error"
        run.error_message = f"{type(exc).__name__}: {exc}"
        new_records = 0
        db.add(run)

    run.finished_at = utcnow()
    check.last_run_at = run.finished_at
    check.last_status = run.status
    db.add(run)
    db.commit()
    db.refresh(run)

    CHECK_RUNS.labels(run.status, check.check_type, triggered_by).inc()
    CHECK_RUN_SECONDS.labels(check.check_type).observe(time.perf_counter() - started)
    if new_records:
        EXCEPTIONS_RECORDED.inc(new_records)
    log.info(
        "check run finished",
        extra={
            "event": "check_run",
            "status": run.status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    return run


def compute_next_run(check: Check, now: datetime) -> datetime | None:
    if check.schedule_kind == "interval" and check.schedule_expr:
        from datetime import timedelta

        return now + timedelta(minutes=max(1, int(float(check.schedule_expr))))
    if check.schedule_kind == "cron" and check.schedule_expr:
        from croniter import croniter

        return croniter(check.schedule_expr, now).get_next(datetime)
    return None
