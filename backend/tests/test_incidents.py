from datetime import timedelta

import pytest

from app.core import notify
from app.core.incidents import process_due_escalations
from app.core.runner import run_check
from app.db import init_db, session_factory
from app.models import Check, Connection, Dataset, Incident, IncidentEvent, NotificationRule, utcnow


class FakeChannel:
    def __init__(self, sink: list):
        self.sink = sink

    def send(self, subject, body, link):
        self.sink.append({"subject": subject, "body": body, "link": link})
        return None


@pytest.fixture(autouse=True)
def _clean_incident_state():
    init_db()
    with session_factory()() as db:
        db.query(IncidentEvent).delete()
        db.query(Incident).delete()
        db.query(NotificationRule).delete()
        db.commit()
    yield


@pytest.fixture
def sends(monkeypatch):
    captured: list = []
    monkeypatch.setattr(notify, "_channel_for", lambda rule: FakeChannel(captured))
    return captured


_seq = 0


def _make_dataset(db, dsn) -> Dataset:
    global _seq
    _seq += 1
    conn = Connection(name=f"incident-src-{_seq}", kind="sqlite", dsn=dsn)
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, table_name="people", display_name="people")
    db.add(ds)
    db.flush()
    return ds


def _make_check(db, ds, *, tolerance=0, last_status=None) -> Check:
    check = Check(
        dataset_id=ds.id,
        name="email not null",
        check_type="not_null",
        column_name="email",
        params={"tolerance": tolerance},
        severity="error",
        status="active",
        last_status=last_status,
    )
    db.add(check)
    db.flush()
    return check


def _add_rule(db, ds_id, **overrides) -> NotificationRule:
    rule = NotificationRule(
        dataset_id=ds_id,
        min_severity="error",
        channel="slack",
        target="https://hook.example/x",
        enabled=True,
        **overrides,
    )
    db.add(rule)
    db.flush()
    return rule


def test_first_failure_creates_incident_and_notifies(source_db, sends):
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id)
        check = _make_check(db, ds, last_status="pass")
        db.commit()
        run = run_check(db, check)

        incident = db.query(Incident).filter(Incident.check_id == check.id).one()
        events = [e.kind for e in incident.events]

    assert run.status == "fail"
    assert incident.status == "open"
    assert incident.occurrence_count == 1
    assert incident.dedupe_key == f"check:{check.id}:failure"
    assert incident.current_run_id == run.id
    assert incident.last_notified_at is not None
    assert "opened" in events
    assert "notified" in events
    assert len(sends) == 1
    assert "failing" in sends[0]["subject"]


def test_first_failure_without_rules_does_not_mark_notified(source_db, sends):
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        check = _make_check(db, ds, last_status="pass")
        db.commit()
        run = run_check(db, check)

        incident = db.query(Incident).filter(Incident.check_id == check.id).one()
        events = [e.kind for e in incident.events]

    assert run.status == "fail"
    assert incident.status == "open"
    assert incident.last_notified_at is None
    assert "notified" not in events
    assert sends == []


def test_rule_added_after_unnotified_failure_pages_on_repeated_failure(source_db, sends):
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        check = _make_check(db, ds, last_status="pass")
        db.commit()
        run_check(db, check)
        incident = db.query(Incident).filter(Incident.check_id == check.id).one()
        assert incident.last_notified_at is None

        _add_rule(db, ds.id)
        db.commit()
        run2 = run_check(db, check)
        db.refresh(incident)
        events = [e.kind for e in incident.events]

    assert run2.status == "fail"
    assert incident.occurrence_count == 2
    assert incident.last_notified_at is not None
    assert "notified" in events
    assert len(sends) == 1
    assert "failing" in sends[0]["subject"]


def test_repeated_failure_dedupes_and_does_not_notify(source_db, sends):
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id)
        check = _make_check(db, ds, last_status="pass")
        db.commit()
        run_check(db, check)
        sends.clear()

        run2 = run_check(db, check)
        incident = db.query(Incident).filter(Incident.check_id == check.id).one()

    assert run2.status == "fail"
    assert incident.status == "open"
    assert incident.occurrence_count == 2
    assert sends == []


def test_recovery_resolves_incident_and_sends_once(source_db, sends):
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id)
        check = _make_check(db, ds, tolerance=0, last_status="pass")
        db.commit()
        run_check(db, check)
        sends.clear()

        check.params = {"tolerance": 10}
        db.commit()
        run = run_check(db, check)
        incident = db.query(Incident).filter(Incident.check_id == check.id).one()
        events = [e.kind for e in incident.events]

    assert run.status == "pass"
    assert incident.status == "resolved"
    assert incident.resolved_at is not None
    assert incident.next_escalation_at is None
    assert "recovered" in events
    assert len(sends) == 1
    assert "recovered" in sends[0]["subject"]


def test_ack_and_resolve_api_role_gates_and_timeline(client, admin_headers, source_db, sends):
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id)
        check = _make_check(db, ds, last_status="pass")
        db.commit()
        run_check(db, check)
        incident_id = db.query(Incident.id).filter(Incident.check_id == check.id).scalar()

    client.post(
        "/api/v1/auth/users",
        json={"email": "incident-viewer@example.com", "password": "viewer123", "role": "viewer"},
        headers=admin_headers,
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"email": "incident-viewer@example.com", "password": "viewer123"},
    ).json()["access_token"]
    viewer_headers = {"Authorization": f"Bearer {token}"}

    assert client.get(f"/api/v1/incidents/{incident_id}", headers=viewer_headers).status_code == 200
    assert client.post(f"/api/v1/incidents/{incident_id}/ack", headers=viewer_headers).status_code == 403

    ack = client.post(
        f"/api/v1/incidents/{incident_id}/ack",
        json={"note": "owning this"},
        headers=admin_headers,
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["status"] == "acknowledged"

    resolved = client.post(
        f"/api/v1/incidents/{incident_id}/resolve",
        json={"note": "fixed upstream"},
        headers=admin_headers,
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "resolved"
    kinds = [event["kind"] for event in body["events"]]
    assert "acknowledged" in kinds
    assert "resolved" in kinds


def test_due_escalation_sends_and_increments_level(source_db, sends):
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id, escalation_delay_minutes=1, max_escalation_level=2)
        check = _make_check(db, ds, last_status="pass")
        db.commit()
        run_check(db, check)
        incident = db.query(Incident).filter(Incident.check_id == check.id).one()
        assert incident.next_escalation_at is not None
        sends.clear()

        incident.next_escalation_at = utcnow() - timedelta(minutes=1)
        db.commit()
        processed = process_due_escalations(db, utcnow())
        db.refresh(incident)

    assert processed == 1
    assert incident.escalation_level == 1
    assert incident.next_escalation_at is not None
    assert len(sends) == 1
    assert "Escalation 1" in sends[0]["subject"]
