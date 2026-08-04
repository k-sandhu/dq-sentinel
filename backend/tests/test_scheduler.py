from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from app.core.runner import compute_next_run
from app.core.scheduler import claim_due_slot, poll_once
from app.db import init_db, session_factory
from app.models import Check, CheckRun, Connection, Dataset, ExceptionRecord, utcnow


def test_compute_next_run_interval_and_cron():
    now = datetime(2026, 6, 9, 10, 30)
    check = Check(schedule_kind="interval", schedule_expr="60")
    assert compute_next_run(check, now) == now + timedelta(minutes=60)

    check = Check(schedule_kind="cron", schedule_expr="0 * * * *")
    assert compute_next_run(check, now) == datetime(2026, 6, 9, 11, 0)

    check = Check(schedule_kind=None, schedule_expr=None)
    assert compute_next_run(check, now) is None


def test_worker_claims_and_runs_due_check(source_db, unique_name):
    init_db()
    factory = session_factory()
    with factory() as db:
        conn = Connection(name=unique_name("sched-src"), kind="sqlite", dsn=source_db)
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


def _sched_fixture(db, unique_name, source_db, **check_kwargs) -> Check:
    """A connection + dataset + one check, straight on the ORM. Caller commits."""
    conn = Connection(name=unique_name("sched-src"), kind="sqlite", dsn=source_db)
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, table_name="people", display_name="people")
    db.add(ds)
    db.flush()
    check = Check(
        dataset_id=ds.id,
        name=unique_name("slot"),
        check_type="not_null",
        column_name="email",
        severity="warn",
        status="active",
        schedule_kind="interval",
        schedule_expr="1440",
        **check_kwargs,
    )
    db.add(check)
    db.flush()
    return check


def test_manual_run_is_not_duplicated_by_the_scheduler(
    client, admin_headers, source_db, unique_name
):
    """#257: a freshly created active check is due immediately, so clicking Run
    left the due slot standing and the worker re-ran the same check seconds later
    — two failures in the same minute (manual + schedule) and x2 recurrence on the
    same sampled rows before an analyst had touched anything.

    The manual run must consume that already-due slot, and the check must still
    run when its NEXT slot comes round (consumed, not starved).
    """
    h = admin_headers
    conn = client.post(
        "/api/v1/connections", json={"name": unique_name("dup-src"), "dsn": source_db}, headers=h
    ).json()
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn["id"], "tables": [{"table_name": "people"}]},
        headers=h,
    ).json()[0]
    resp = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": ds["id"],
            "name": unique_name("dup email not_null"),
            "check_type": "not_null",
            "column_name": "email",
            "severity": "error",
            "schedule_kind": "interval",
            "schedule_expr": "1440",  # "daily", as in the report
            "status": "active",
        },
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    check_id = resp.json()["id"]

    run = client.post(f"/api/v1/checks/{check_id}/run", headers=h)
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "fail"

    with ThreadPoolExecutor(max_workers=1) as executor:
        poll_once(executor)

    factory = session_factory()
    with factory() as db:
        triggers = [
            r.triggered_by
            for r in db.query(CheckRun).filter(CheckRun.check_id == check_id).order_by(CheckRun.id)
        ]
        assert triggers == ["manual"], f"the manual run was duplicated by the scheduler: {triggers}"
        recurrence = [
            r.occurrence_count
            for r in db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id)
        ]
        assert recurrence and set(recurrence) == {1}, f"recurrence inflated on first sight: {recurrence}"
        check = db.get(Check, check_id)
        assert check.next_run_at is not None, "manual run parked the schedule"
        assert check.next_run_at > utcnow(), "due slot was not consumed"

        # Not starved: when the next slot falls due the worker still runs it.
        #
        # Backdate hard rather than by a second. The claim query is
        # `ORDER BY next_run_at LIMIT 20`, and this suite shares one app DB across
        # every file, so by the time this runs there are other active checks left
        # due by earlier tests. A check that is one second overdue sorts BEHIND
        # them and falls outside the batch — the test then passes alone and fails
        # in the full suite. Being the most-overdue check makes the claim
        # deterministic without weakening what is asserted.
        check.next_run_at = utcnow() - timedelta(days=365)
        db.commit()

    with ThreadPoolExecutor(max_workers=1) as executor:
        poll_once(executor)

    with factory() as db:
        triggers = [
            r.triggered_by
            for r in db.query(CheckRun).filter(CheckRun.check_id == check_id).order_by(CheckRun.id)
        ]
        assert triggers == ["manual", "schedule"], f"check was starved by its manual run: {triggers}"


def test_manual_run_does_not_postpone_a_future_slot(source_db, unique_name):
    """Running manually at 09:00 must not push a 10:00 slot out by a day: only an
    ALREADY-DUE slot is consumed, a future one is left exactly where it is (#257).
    """
    init_db()
    factory = session_factory()
    with factory() as db:
        slot = utcnow() + timedelta(minutes=30)
        check = _sched_fixture(db, unique_name, source_db, next_run_at=slot)
        db.commit()
        assert claim_due_slot(db, check) is False
        db.refresh(check)
        assert check.next_run_at == slot


def test_claim_due_slot_covers_every_slot_poll_once_treats_as_due(source_db, unique_name):
    """``poll_once`` initializes an active+scheduled check with no next_run_at to
    ``now`` and claims it in the SAME pass, so the manual path has to consume that
    shape too — otherwise the duplicate run just moves one poll later. A check
    with no schedule has no slot to consume at all."""
    init_db()
    factory = session_factory()
    with factory() as db:
        pending = _sched_fixture(db, unique_name, source_db, next_run_at=None)
        unscheduled = _sched_fixture(db, unique_name, source_db, next_run_at=None)
        unscheduled.schedule_expr = None
        db.commit()

        assert claim_due_slot(db, pending) is True
        assert pending.next_run_at > utcnow()
        assert claim_due_slot(db, unscheduled) is False
        assert unscheduled.next_run_at is None


def test_claim_due_slot_is_won_by_exactly_one_caller(source_db, unique_name):
    """The manual path claims through the same optimistic CAS as the worker, so a
    worker that read the same slot loses it rather than double-running (#257)."""
    init_db()
    factory = session_factory()
    with factory() as db:
        check = _sched_fixture(
            db, unique_name, source_db, next_run_at=utcnow() - timedelta(minutes=5)
        )
        db.commit()
        check_id = check.id

    with factory() as db_a, factory() as db_b:
        a, b = db_a.get(Check, check_id), db_b.get(Check, check_id)  # both read the same due slot
        assert claim_due_slot(db_a, a) is True
        assert claim_due_slot(db_b, b) is False  # CAS loser: someone else owns this slot

    with factory() as db:
        assert db.get(Check, check_id).next_run_at > utcnow()


def test_validate_schedule_rejects_unparseable_expr():
    from app.core.check_authoring import validate_schedule

    validate_schedule("interval", "60")  # ok
    validate_schedule("cron", "0 6 * * *")  # ok
    validate_schedule("interval", None)  # no schedule -> fine
    for kind, expr in [("interval", "daily"), ("cron", "every day"), ("cron", "0 6 * *")]:
        try:
            validate_schedule(kind, expr)
        except ValueError:
            continue
        raise AssertionError(f"validate_schedule accepted bad {kind} expr {expr!r}")


def test_poisoned_schedule_expr_does_not_wedge_pass(source_db, unique_name):
    """A single unparseable schedule_expr (pre-validation legacy row) must be parked,
    not abort the whole claim loop and starve every check behind it."""
    init_db()
    factory = session_factory()
    with factory() as db:
        conn = Connection(name=unique_name("sched-poison"), kind="sqlite", dsn=source_db)
        db.add(conn)
        db.flush()
        ds = Dataset(connection_id=conn.id, table_name="people", display_name="people")
        db.add(ds)
        db.flush()
        # The poisoned check is due earliest, so it sorts first in the `due` query —
        # exactly the position that used to abort the pass before any sibling ran.
        bad = Check(
            dataset_id=ds.id, name="poison", check_type="not_null", column_name="email",
            severity="warn", status="active", schedule_kind="cron", schedule_expr="not a cron",
            next_run_at=datetime.now() - timedelta(minutes=10),
        )
        good = Check(
            dataset_id=ds.id, name="good", check_type="not_null", column_name="email",
            severity="warn", status="active", schedule_kind="interval", schedule_expr="60",
            next_run_at=datetime.now() - timedelta(minutes=5),
        )
        db.add_all([bad, good])
        db.commit()
        bad_id, good_id = bad.id, good.id

    with ThreadPoolExecutor(max_workers=1) as executor:
        claimed = poll_once(executor)
    assert claimed >= 1  # the good check still ran despite the poisoned one sorting first

    with factory() as db:
        assert db.get(Check, bad_id).next_run_at is None  # parked, no longer blocking
        assert db.query(CheckRun).filter(CheckRun.check_id == good_id).count() == 1


def test_request_stop_sets_stop_event():
    from app.core import scheduler

    scheduler._STOP.clear()
    try:
        assert not scheduler._STOP.is_set()
        scheduler.request_stop()
        assert scheduler._STOP.is_set()
    finally:
        scheduler._STOP.clear()


def test_run_forever_drains_and_exits_on_stop(monkeypatch):
    """run_forever exits promptly once stop is requested, after at least one pass.

    Drives _STOP directly: signal handlers can't be installed off the main thread,
    which is exactly the ValueError branch the loop tolerates.
    """
    import threading
    import time

    from app.core import scheduler

    scheduler._STOP.clear()
    passes: list[int] = []
    monkeypatch.setattr(scheduler, "poll_once", lambda _ex: (passes.append(1), 0)[1])

    t = threading.Thread(target=scheduler.run_forever, daemon=True)
    t.start()
    try:
        for _ in range(100):  # wait up to ~2s for the first poll
            if passes:
                break
            time.sleep(0.02)
        scheduler.request_stop()
        t.join(timeout=5)
        assert not t.is_alive(), "run_forever did not exit after stop was requested"
        assert passes, "run_forever never polled"
    finally:
        scheduler.request_stop()  # ensure the thread can't outlive the test
        t.join(timeout=5)
        scheduler._STOP.clear()


# --- worker readiness (#311) --------------------------------------------------
# The container healthcheck must answer "can this worker claim a check?", not
# "did a socket bind?". `app.worker` binds the metrics port BEFORE init_db() on
# purpose (a worker wedged in a migration must stay scrapeable), so readiness is
# carried by the dq_worker_up gauge, which app.core.scheduler sets only once
# init_db() has returned and run_forever() has been entered.


def test_worker_binds_metrics_before_migrating_but_is_not_ready_until_the_loop(monkeypatch):
    """A worker still inside init_db() must not report healthy, even though its
    metrics endpoint is already answering."""
    from prometheus_client import generate_latest

    import app.db
    import app.observability
    from app import worker
    from app.config import get_settings
    from app.core import scheduler
    from app.observability import WORKER_UP

    WORKER_UP.set(0)
    events: list[str] = []
    ready_mid_migration: list[bool] = []

    monkeypatch.setattr(worker, "start_http_server", lambda port: events.append(f"metrics:{port}"))
    monkeypatch.setattr(app.observability, "configure_logging", lambda *a, **k: None)

    def fake_init_db() -> None:
        # We are inside the migration, with the metrics endpoint already serving.
        events.append("init_db")
        ready_mid_migration.append(worker._readiness_ok(generate_latest().decode()))

    monkeypatch.setattr(app.db, "init_db", fake_init_db)
    monkeypatch.setattr(scheduler, "run_forever", lambda: events.append("run_forever"))

    assert worker.main([]) == 0
    port = get_settings().worker_metrics_port
    # Metrics first (diagnosability), then migrate, then the loop.
    assert events == [f"metrics:{port}", "init_db", "run_forever"]
    assert ready_mid_migration == [False]


def test_worker_healthcheck_flag_reflects_the_readiness_gauge(monkeypatch):
    """End-to-end probe against a real metrics endpoint: an open port with no
    scheduler behind it exits non-zero (the old probe passed here)."""
    from prometheus_client import start_http_server

    from app import worker
    from app.config import get_settings
    from app.observability import WORKER_UP

    WORKER_UP.set(0)
    httpd, _thread = start_http_server(0, addr="127.0.0.1")  # ephemeral loopback port
    try:
        settings = get_settings().model_copy(update={"worker_metrics_port": httpd.server_port})
        monkeypatch.setattr(worker, "get_settings", lambda: settings)
        assert worker.main(["--healthcheck"]) == 1  # serving, but not scheduling
        WORKER_UP.set(1)
        assert worker.main(["--healthcheck"]) == 0
    finally:
        WORKER_UP.set(0)
        httpd.shutdown()
        httpd.server_close()


def test_worker_rejects_an_unrecognized_flag_instead_of_starting_a_scheduler():
    """A typo'd probe flag must not fall through and boot a second scheduler
    (which would migrate, bind the metrics port and claim checks) inside what the
    operator believed was a healthcheck."""
    from app import worker

    assert worker.main(["--healthchek"]) == 2


def test_run_forever_satisfies_the_worker_readiness_probe(monkeypatch):
    """Close the loop: the predicate the healthcheck uses is actually satisfied by
    the real scheduler loop, and only while it is running."""
    import threading
    import time

    from prometheus_client import generate_latest

    from app import worker
    from app.core import scheduler
    from app.observability import WORKER_UP

    WORKER_UP.set(0)
    scheduler._STOP.clear()
    assert not worker._readiness_ok(generate_latest().decode())  # before the loop

    ready_in_loop: list[bool] = []
    monkeypatch.setattr(
        scheduler,
        "poll_once",
        lambda _ex: (ready_in_loop.append(worker._readiness_ok(generate_latest().decode())), 0)[1],
    )

    t = threading.Thread(target=scheduler.run_forever, daemon=True)
    t.start()
    try:
        for _ in range(100):  # wait up to ~2s for the first poll
            if ready_in_loop:
                break
            time.sleep(0.02)
        scheduler.request_stop()
        t.join(timeout=5)
    finally:
        scheduler.request_stop()
        t.join(timeout=5)
        scheduler._STOP.clear()

    assert ready_in_loop and all(ready_in_loop), "worker did not report ready inside the loop"
    assert not worker._readiness_ok(generate_latest().decode())  # 0 again after the drain


def test_compose_worker_healthcheck_probes_readiness_not_just_the_port():
    """The shipped healthcheck must assert readiness. Probing :9100 for a 200
    flips the container healthy before migrations have run, so
    `docker compose up --wait` returned before the worker could claim anything."""
    import yaml

    from app.config import REPO_DIR

    compose = yaml.safe_load((REPO_DIR / "docker-compose.yml").read_text(encoding="utf-8"))
    worker_svc = compose["services"]["worker"]
    assert worker_svc["healthcheck"]["test"] == [
        "CMD", "python", "-m", "app.worker", "--healthcheck",
    ]
