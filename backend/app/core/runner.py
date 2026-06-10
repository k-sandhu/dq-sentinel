"""Execute checks and persist runs + exception records."""

import logging
import time
from datetime import datetime

from sqlalchemy.orm import Session

from app.connectors.sa import connector_for
from app.core.check_types import CheckContext, CheckResult, run_check_type
from app.models import Check, CheckRun, ExceptionRecord, utcnow
from app.observability import CHECK_RUN_SECONDS, CHECK_RUNS, EXCEPTIONS_RECORDED

log = logging.getLogger(__name__)


def _status_for(check: Check, result: CheckResult) -> str:
    tolerance = int(check.params.get("tolerance", 0) or 0)
    if result.violation_count <= tolerance:
        return "pass"
    return {"info": "warn", "warn": "warn", "error": "fail"}.get(check.severity, "fail")


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
        run.metrics = metrics

        for i, row in enumerate(result.sample_rows):
            run.exceptions.append(
                ExceptionRecord(
                    check_id=check.id,
                    dataset_id=dataset.id,
                    row_data=row,
                    reason=result.reasons[i] if i < len(result.reasons) else result.detail or check.name,
                    outlier_score=result.scores[i] if i < len(result.scores) else None,
                )
            )
    except Exception as exc:  # noqa: BLE001 - run must record any failure
        log.exception("Check %s (%s) errored", check.id, check.name)
        run.status = "error"
        run.error_message = f"{type(exc).__name__}: {exc}"

    run.finished_at = utcnow()
    check.last_run_at = run.finished_at
    check.last_status = run.status
    db.add(run)
    db.commit()
    db.refresh(run)

    CHECK_RUNS.labels(run.status, check.check_type, triggered_by).inc()
    CHECK_RUN_SECONDS.labels(check.check_type).observe(time.perf_counter() - started)
    if run.exceptions:
        EXCEPTIONS_RECORDED.inc(len(run.exceptions))
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
