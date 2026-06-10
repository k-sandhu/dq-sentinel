from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from app.core.runner import compute_next_run
from app.core.scheduler import poll_once
from app.db import init_db, session_factory
from app.models import Check, CheckRun, Connection, Dataset


def test_compute_next_run_interval_and_cron():
    now = datetime(2026, 6, 9, 10, 30)
    check = Check(schedule_kind="interval", schedule_expr="60")
    assert compute_next_run(check, now) == now + timedelta(minutes=60)

    check = Check(schedule_kind="cron", schedule_expr="0 * * * *")
    assert compute_next_run(check, now) == datetime(2026, 6, 9, 11, 0)

    check = Check(schedule_kind=None, schedule_expr=None)
    assert compute_next_run(check, now) is None


def test_worker_claims_and_runs_due_check(source_db):
    init_db()
    factory = session_factory()
    with factory() as db:
        conn = Connection(name="sched-src", kind="sqlite", dsn=source_db)
        db.add(conn)
        db.flush()
        ds = Dataset(connection_id=conn.id, table_name="people", display_name="people")
        db.add(ds)
        db.flush()
        check = Check(
            dataset_id=ds.id,
            name="sched not_null email",
            check_type="not_null",
            column_name="email",
            severity="warn",
            status="active",
            schedule_kind="interval",
            schedule_expr="60",
            next_run_at=datetime.now() - timedelta(minutes=5),
        )
        db.add(check)
        db.commit()
        check_id = check.id

    with ThreadPoolExecutor(max_workers=1) as executor:
        claimed = poll_once(executor)
    assert claimed >= 1

    with factory() as db:
        check = db.get(Check, check_id)
        assert check.last_run_at is not None
        assert check.next_run_at > datetime.now()  # rescheduled into the future
        runs = db.query(CheckRun).filter(CheckRun.check_id == check_id).all()
        assert len(runs) == 1
        assert runs[0].triggered_by == "schedule"
        assert runs[0].violation_count == 5

        # nothing due anymore -> second pass claims nothing for this check
    with ThreadPoolExecutor(max_workers=1) as executor:
        again = poll_once(executor)
    with factory() as db:
        runs = db.query(CheckRun).filter(CheckRun.check_id == check_id).count()
    assert runs == 1, f"check ran again unexpectedly (claimed={again})"
