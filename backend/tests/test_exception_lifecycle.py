"""Exception identity, recurrence tracking, and lifecycle reconciliation (#55).

Drives the real `run_check` flow against the synthetic source DB so the
fingerprint/reconcile/auto-resolve path is exercised end to end.
"""

import uuid

from app.core.runner import exception_fingerprint, run_check
from app.db import init_db, session_factory
from app.models import (
    Check,
    Connection,
    Dataset,
    ExceptionEvent,
    ExceptionRecord,
    Profile,
    User,
)


def _setup(db, source_db, *, check_type="not_null", column="email", params=None, profile_pk=None):
    conn = Connection(name=f"lc-{uuid.uuid4().hex}", kind="sqlite", dsn=source_db)
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, table_name="people", display_name="people")
    db.add(ds)
    db.flush()
    if profile_pk is not None:
        db.add(
            Profile(
                dataset_id=ds.id,
                row_count=200,
                sampled_rows=200,
                columns=[],
                table_facts={"pk_candidates": profile_pk},
            )
        )
    check = Check(
        dataset_id=ds.id,
        name="lifecycle check",
        check_type=check_type,
        column_name=column,
        severity="error",
        status="active",
        params=params or {},
    )
    db.add(check)
    db.commit()
    db.refresh(check)
    return check


def test_fingerprint_stable_and_check_scoped():
    row = {"id": 7, "email": "x@y.com", "age": 30}
    # Stable across calls.
    assert exception_fingerprint(5, row, ["id"]) == exception_fingerprint(5, row, ["id"])
    # Same row, different check -> different fingerprint.
    assert exception_fingerprint(5, row, ["id"]) != exception_fingerprint(6, row, ["id"])
    # With vs without pk: PK-only ignores drift in non-key columns.
    pk_only = exception_fingerprint(5, {"id": 7, "age": 99}, ["id"])
    assert pk_only == exception_fingerprint(5, {"id": 7, "age": 1}, ["id"])
    # Without pk, the whole row participates (so the two above differ).
    assert exception_fingerprint(5, {"id": 7, "age": 99}, None) != exception_fingerprint(
        5, {"id": 7, "age": 1}, None
    )
    # None pk and empty pk behave the same (fall back to sorted row).
    assert exception_fingerprint(5, row, None) == exception_fingerprint(5, row, [])


def test_recurring_row_updates_in_place(source_db):
    init_db()
    factory = session_factory()
    with factory() as db:
        check = _setup(db, source_db)
        check_id = check.id
        run1 = run_check(db, check, triggered_by="manual")
        assert run1.status == "fail"
        run1_id = run1.id
        first_count = db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).count()
        assert first_count == 5  # NULL_EMAILS

    with factory() as db:
        check = db.get(Check, check_id)
        run2 = run_check(db, check, triggered_by="schedule")
        recs = db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).all()
        # No new rows — same identities reconciled.
        assert len(recs) == 5
        assert all(r.occurrence_count == 2 for r in recs)
        assert all(r.last_run_id == run2.id for r in recs)
        # run_id still points at the run that first captured the row.
        assert all(r.run_id == run1_id for r in recs)
        assert all(r.last_seen_at >= r.first_seen_at for r in recs)


def test_tolerated_violations_do_not_record_or_thrash(source_db):
    # 5 NULL emails, tolerance 10 -> the run PASSES. Tolerated rows must not be
    # recorded as exceptions and then auto-resolved every run (open->resolved churn).
    init_db()
    factory = session_factory()
    with factory() as db:
        check = _setup(db, source_db, params={"tolerance": 10})
        check_id = check.id
        # Baseline event count (the app DB is session-shared, so measure the delta).
        events_before = db.query(ExceptionEvent).count()
        run1 = run_check(db, check, triggered_by="manual")
        assert run1.status == "pass"
        assert db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).count() == 0

    with factory() as db:
        check = db.get(Check, check_id)
        run_check(db, check, triggered_by="schedule")
        # Still nothing recorded, and crucially no regression/resolve event churn:
        # the two runs must not have created a single exception event.
        assert db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).count() == 0
        assert db.query(ExceptionEvent).count() == events_before


def test_expected_row_is_suppressed_not_reinserted(source_db):
    init_db()
    factory = session_factory()
    with factory() as db:
        check = _setup(db, source_db)
        check_id = check.id
        run_check(db, check, triggered_by="manual")
        recs = db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).all()
        for r in recs:
            r.status = "expected"
        db.commit()
        n = len(recs)

    with factory() as db:
        check = db.get(Check, check_id)
        run2 = run_check(db, check, triggered_by="schedule")
        recs = db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).all()
        assert len(recs) == n  # no new records
        assert all(r.status == "expected" for r in recs)  # status unchanged
        assert run2.metrics.get("suppressed") == n


def test_resolved_row_regresses_to_open(source_db):
    init_db()
    factory = session_factory()
    with factory() as db:
        check = _setup(db, source_db)
        check_id = check.id
        run_check(db, check, triggered_by="manual")
        recs = db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).all()
        for r in recs:
            r.status = "resolved"
            r.assigned_to_id = None
            r.note = "we fixed this by backfilling"
        db.commit()
        n = len(recs)

    with factory() as db:
        check = db.get(Check, check_id)
        run2 = run_check(db, check, triggered_by="schedule")
        recs = db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).all()
        assert len(recs) == n
        assert all(r.status == "open" for r in recs)  # reopened
        # Regression preserves the prior resolution note (analyst context).
        assert all(r.note == "we fixed this by backfilling" for r in recs)
        assert run2.metrics.get("regressed") == n
        # A machine-attributable system event was written.
        for r in recs:
            evs = (
                db.query(ExceptionEvent)
                .filter(ExceptionEvent.exception_id == r.id, ExceptionEvent.kind == "system")
                .all()
            )
            assert any("regressed" in e.comment for e in evs)


def test_passing_run_auto_resolves_only_open(source_db):
    """A check that fails, gets various triage states, then passes."""
    init_db()
    factory = session_factory()
    with factory() as db:
        # Use a strict not_null on email so it fails first.
        check = _setup(db, source_db)
        check_id = check.id
        run_check(db, check, triggered_by="manual")
        recs = db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).all()
        assert len(recs) == 5
        # One of each non-open, deliberate state; leave the rest open.
        recs[0].status = "acknowledged"
        recs[1].status = "expected"
        recs[2].status = "muted"
        # recs[3], recs[4] stay open. An analyst leaves a note + attribution on an
        # OPEN record — it must survive auto-resolve (#156).
        admin = db.query(User).filter(User.email == "admin@example.com").first()
        recs[3].note = "investigating with upstream owner"
        recs[3].marked_by_id = admin.id
        open_with_note_id = recs[3].id
        open_with_note_ver = recs[3].version
        db.commit()

    # Now flip the check to a column with no nulls (id) so the run passes.
    with factory() as db:
        check = db.get(Check, check_id)
        check.column_name = "id"
        db.commit()

    with factory() as db:
        check = db.get(Check, check_id)
        run2 = run_check(db, check, triggered_by="schedule")
        assert run2.status == "pass"
        by_status: dict[str, int] = {}
        for r in db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).all():
            by_status[r.status] = by_status.get(r.status, 0) + 1
        # The 2 open ones became resolved; the deliberate states survived.
        assert by_status.get("resolved") == 2
        assert by_status.get("acknowledged") == 1
        assert by_status.get("expected") == 1
        assert by_status.get("muted") == 1
        # Auto-resolve records a machine system event but must NOT clobber the
        # analyst's note/attribution on the previously-open record (#156).
        resolved = [
            r
            for r in db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).all()
            if r.status == "resolved"
        ]
        for r in resolved:
            assert (
                db.query(ExceptionEvent)
                .filter(
                    ExceptionEvent.exception_id == r.id,
                    ExceptionEvent.comment == "auto-resolved: check passing",
                )
                .count()
                == 1
            )
        kept = db.get(ExceptionRecord, open_with_note_id)
        assert kept.status == "resolved"
        assert kept.note == "investigating with upstream owner"  # analyst note survived
        assert kept.marked_by_id is not None  # attribution survived
        assert kept.version == open_with_note_ver + 1  # version bumped by auto-resolve


def test_fingerprint_used_with_profile_pk(source_db):
    """With a profile that names a pk candidate, identity uses only the pk col."""
    init_db()
    factory = session_factory()
    with factory() as db:
        check = _setup(db, source_db, profile_pk=["id"])
        check_id = check.id
        run_check(db, check, triggered_by="manual")
        recs = db.query(ExceptionRecord).filter(ExceptionRecord.check_id == check_id).all()
        assert all(r.fingerprint is not None for r in recs)
        # Fingerprint matches the pk-only computation for the stored row.
        for r in recs:
            assert r.fingerprint == exception_fingerprint(check_id, r.row_data, ["id"])


def test_init_db_idempotent():
    # Migration-based init_db (#23) must be safe to call repeatedly:
    # `alembic upgrade head` is a no-op once the DB is already at head.
    init_db()
    init_db()
