"""Check scheduler CAS and fencing on an explicitly supplied PostgreSQL metadata DB.

Run after Alembic upgrade with DQ_DATABASE_URL set. Creates and removes a uniquely
named synthetic connection, dataset and check. It never opens a source database.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from uuid import uuid4

from sqlalchemy import delete, event

from app.core.runner import StaleClaimError, _finish_claim
from app.core.scheduler import _claim_scheduled, _recover_expired
from app.db import session_factory
from app.models import Check, Connection, Dataset, utcnow


def main() -> None:
    factory = session_factory()
    now = utcnow()
    with factory() as db:
        if db.bind.dialect.name != "postgresql":
            raise RuntimeError("This verification requires a PostgreSQL metadata database")
        conn = Connection(name=f"lease-ci-{uuid4()}", kind="sqlite", dsn="sqlite:///unused")
        db.add(conn)
        db.flush()
        ds = Dataset(connection_id=conn.id, table_name="unused")
        db.add(ds)
        db.flush()
        check = Check(dataset_id=ds.id, name="lease-ci", check_type="not_null", status="active",
                      schedule_kind="interval", schedule_expr="1", next_run_at=now)
        db.add(check)
        db.commit()
        conn_id, dataset_id, check_id = conn.id, ds.id, check.id
    try:
        barrier = Barrier(2, timeout=10)

        def compete() -> str | None:
            with factory() as db:
                check = db.get(Check, check_id)
                barrier.wait()
                return _claim_scheduled(db, check, now, now + timedelta(minutes=1))

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _: compete(), range(2)))
        winners = [token for token in claims if token]
        assert len(winners) == 1, claims
        old = winners[0]
        # Completion locks the row. Recovery has already read the old token and
        # blocks on its CAS; after commit PostgreSQL must re-evaluate its WHERE.
        update_started = Event()
        with factory() as owner, ThreadPoolExecutor(max_workers=1) as executor:
            _finish_claim(owner, check_id, old)

            def recover_while_locked() -> None:
                with factory() as contender:
                    connection = contender.connection()

                    def signal_update(_conn, _cursor, statement, _params, _context, _many):
                        if statement.lstrip().upper().startswith("UPDATE CHECKS"):
                            update_started.set()

                    event.listen(connection, "before_cursor_execute", signal_update)
                    _recover_expired(contender, now + timedelta(days=1), check_id=check_id)

            future = executor.submit(recover_while_locked)
            reached_update = update_started.wait(10)
            owner.commit()
            future.result(timeout=10)
            assert reached_update, "recovery did not reach its update"
        with factory() as db:
            check = db.get(Check, check_id)
            assert check.claim_token is None
            assert check.next_run_at == now + timedelta(minutes=1)
            check.next_run_at = now
            db.commit()
            old = _claim_scheduled(db, check, now, now + timedelta(minutes=1))
            assert old
            _recover_expired(db, now + timedelta(days=1), check_id=check_id)
            db.refresh(check)
            new = _claim_scheduled(db, check, now + timedelta(days=1), now + timedelta(days=2))
            assert new and new != old
        with factory() as db:
            try:
                _finish_claim(db, check_id, old)
            except StaleClaimError:
                db.rollback()
            else:
                raise AssertionError("stale owner cleared replacement claim")
            assert db.get(Check, check_id).claim_token == new
            _finish_claim(db, check_id, new)
            db.commit()
        with factory() as db:
            assert db.get(Check, check_id).claim_token is None
        print("PostgreSQL scheduler claim and fencing checks passed")
    finally:
        with factory() as db:
            db.execute(delete(Check).where(Check.id == check_id))
            db.execute(delete(Dataset).where(Dataset.id == dataset_id))
            db.execute(delete(Connection).where(Connection.id == conn_id))
            db.commit()


if __name__ == "__main__":
    main()
