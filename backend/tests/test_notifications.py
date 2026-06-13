"""Notifications on failed runs (issue #27).

Covers the transition matrix (the anti-spam decision), rule matching + severity
gate, disabled rules, channel-failure isolation (a dead channel must never fail a
run), and admin-only CRUD. A fake channel captures sends so no real Slack/SMTP is
touched; we drive the real ``run_check`` so the transition logic is exercised
end-to-end through the DB.
"""

import pytest

from app.core import notify
from app.core.runner import run_check
from app.db import init_db, session_factory
from app.models import Check, Connection, Dataset, NotificationRule


class FakeChannel:
    """Records every send; raises on demand to simulate a dead channel."""

    def __init__(self, sink: list, *, fail: bool = False):
        self.sink = sink
        self.fail = fail

    def send(self, subject, body, link):
        self.sink.append({"subject": subject, "body": body, "link": link})
        if self.fail:
            raise RuntimeError("channel is down")


@pytest.fixture(autouse=True)
def _clean_rules():
    """Rules persist in the shared app DB; a leaked global rule would fire for
    every later test. Wipe them before each test so cases are independent."""
    init_db()
    with session_factory()() as db:
        db.query(NotificationRule).delete()
        db.commit()
    yield


@pytest.fixture
def sends(monkeypatch):
    """Patch channel construction so every matched rule routes to a FakeChannel
    that appends to the returned list. Returns the captured-sends list."""
    captured: list = []
    monkeypatch.setattr(notify, "_channel_for", lambda rule: FakeChannel(captured))
    return captured


@pytest.fixture
def failing_sends(monkeypatch):
    """Like ``sends`` but the channel raises — to prove a run still commits."""
    captured: list = []
    monkeypatch.setattr(notify, "_channel_for", lambda rule: FakeChannel(captured, fail=True))
    return captured


_conn_seq = 0


def _make_dataset(db, dsn) -> Dataset:
    global _conn_seq
    _conn_seq += 1
    conn = Connection(name=f"notify-src-{_conn_seq}", kind="sqlite", dsn=dsn)
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, table_name="people", display_name="people")
    db.add(ds)
    db.flush()
    return ds


def _make_check(db, ds, *, severity="error", tolerance=0, last_status=None) -> Check:
    check = Check(
        dataset_id=ds.id,
        name="email not null",
        check_type="not_null",
        column_name="email",
        params={"tolerance": tolerance},
        severity=severity,
        status="active",
        last_status=last_status,
    )
    db.add(check)
    db.flush()
    return check


def _add_rule(db, ds_id, *, min_severity="error", channel="slack", target="https://hook.example/x", enabled=True, on_error_runs=True):
    rule = NotificationRule(
        dataset_id=ds_id,
        min_severity=min_severity,
        channel=channel,
        target=target,
        enabled=enabled,
        on_error_runs=on_error_runs,
    )
    db.add(rule)
    db.flush()
    return rule


# --------------------------------------------------------------- transition matrix
def test_pass_to_fail_fires(source_db, sends):
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id)
        check = _make_check(db, ds, tolerance=0, last_status="pass")  # 5 nulls -> fail
        db.commit()
        run = run_check(db, check)
    assert run.status == "fail"
    assert len(sends) == 1
    assert "failing" in sends[0]["subject"]
    assert sends[0]["link"].endswith(f"/datasets/{ds.id}/exceptions")


def test_first_ever_fail_fires(source_db, sends):
    """None (never run) -> fail must notify."""
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id)
        check = _make_check(db, ds, tolerance=0, last_status=None)
        db.commit()
        run = run_check(db, check)
    assert run.status == "fail"
    assert len(sends) == 1


def test_fail_to_fail_is_silent(source_db, sends):
    """The rule that prevents alert fatigue: a still-failing check is silent."""
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id)
        check = _make_check(db, ds, tolerance=0, last_status="fail")
        db.commit()
        run = run_check(db, check)
    assert run.status == "fail"
    assert sends == []


def test_fail_to_pass_fires_recovery(source_db, sends):
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id)
        check = _make_check(db, ds, tolerance=10, last_status="fail")  # 5 nulls <= 10 -> pass
        db.commit()
        run = run_check(db, check)
    assert run.status == "pass"
    assert len(sends) == 1
    assert "recovered" in sends[0]["subject"]


def test_pass_to_pass_is_silent(source_db, sends):
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id)
        check = _make_check(db, ds, tolerance=10, last_status="pass")
        db.commit()
        run = run_check(db, check)
    assert run.status == "pass"
    assert sends == []


# --------------------------------------------------------------- rule matching / gate
def test_dataset_specific_rule_matches_only_its_dataset(source_db, sends):
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        other = _make_dataset(db, source_db)
        _add_rule(db, other.id)  # rule for a different dataset
        check = _make_check(db, ds, tolerance=0, last_status="pass")
        db.commit()
        run_check(db, check)
    assert sends == []  # nothing matches this dataset


def test_global_rule_matches_all_datasets(source_db, sends):
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, None)  # dataset_id None = all datasets
        check = _make_check(db, ds, tolerance=0, last_status="pass")
        db.commit()
        run_check(db, check)
    assert len(sends) == 1


def test_severity_gate_warn_rule_ignores_info_check(source_db, sends):
    """A rule requiring >= warn must not fire for an info-severity check.

    An info check that violates produces status 'warn' (never 'fail'), and the
    pass/warn/None -> fail/error transition does not include warn outcomes, so we
    assert the gate directly: even forcing a transition, the rule won't match."""
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id, min_severity="warn")
        # info severity -> SEVERITY_RANK 0, below the rule's warn (1)
        check = _make_check(db, ds, severity="info", tolerance=0, last_status="pass")
        db.flush()
        matched = notify._matching_rules(db, check)
    assert matched == []


def test_severity_gate_error_rule_matches_error_check(source_db, sends):
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id, min_severity="warn")  # warn rule
        check = _make_check(db, ds, severity="error", tolerance=0, last_status="pass")  # error >= warn
        db.commit()
        run_check(db, check)
    assert len(sends) == 1


def test_disabled_rule_is_silent(source_db, sends):
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id, enabled=False)
        check = _make_check(db, ds, tolerance=0, last_status="pass")
        db.commit()
        run_check(db, check)
    assert sends == []


# --------------------------------------------------------------- isolation
def test_channel_failure_does_not_fail_run(source_db, failing_sends):
    """A raising channel is swallowed: the run still commits with its status."""
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        _add_rule(db, ds.id)
        check = _make_check(db, ds, tolerance=0, last_status="pass")
        db.commit()
        run = run_check(db, check)
        run_id = run.id
        assert run.status == "fail"
        assert len(failing_sends) == 1  # send was attempted
    # run is durably committed despite the channel exception
    with session_factory()() as db:
        from app.models import CheckRun

        persisted = db.get(CheckRun, run_id)
        assert persisted is not None
        assert persisted.status == "fail"


def test_no_rules_no_sends(source_db, sends):
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        check = _make_check(db, ds, tolerance=0, last_status="pass")
        db.commit()
        run_check(db, check)
    assert sends == []


# --------------------------------------------------------------- channel builders
def test_channel_for_slack_uses_global_default_when_target_blank(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DQ_NOTIFY_SLACK_WEBHOOK_URL", "https://global.example/hook")
    get_settings.cache_clear()
    rule = NotificationRule(channel="slack", target="", min_severity="error")
    ch = notify._channel_for(rule)
    assert isinstance(ch, notify.SlackWebhook)
    assert ch.url == "https://global.example/hook"
    get_settings.cache_clear()


def test_channel_for_slack_none_without_target_or_default(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("DQ_NOTIFY_SLACK_WEBHOOK_URL", "")
    get_settings.cache_clear()
    rule = NotificationRule(channel="slack", target="", min_severity="error")
    assert notify._channel_for(rule) is None
    get_settings.cache_clear()


def test_channel_for_email_parses_recipients():
    rule = NotificationRule(channel="email", target="a@x.com, b@y.com ,", min_severity="error")
    ch = notify._channel_for(rule)
    assert isinstance(ch, notify.SmtpEmail)
    assert ch.recipients == ["a@x.com", "b@y.com"]


# --------------------------------------------------------------- admin-only CRUD
def test_crud_and_admin_only(client, admin_headers, source_db):
    # need a real dataset id to attach a rule to
    init_db()
    with session_factory()() as db:
        ds = _make_dataset(db, source_db)
        db.commit()
        ds_id = ds.id

    # create (admin)
    resp = client.post(
        "/api/v1/notifications/rules",
        json={"dataset_id": ds_id, "channel": "slack", "target": "https://hook.example/abc", "min_severity": "warn"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    rule = resp.json()
    assert rule["channel"] == "slack"
    assert rule["dataset_name"] == "people"

    # list (admin can read)
    listed = client.get("/api/v1/notifications/rules", headers=admin_headers)
    assert listed.status_code == 200
    assert any(r["id"] == rule["id"] for r in listed.json())

    # patch
    patched = client.patch(
        f"/api/v1/notifications/rules/{rule['id']}", json={"enabled": False}, headers=admin_headers
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    # bad dataset_id rejected
    assert client.post(
        "/api/v1/notifications/rules",
        json={"dataset_id": 999999, "channel": "slack", "target": "https://x"},
        headers=admin_headers,
    ).status_code == 422

    # email rule with empty target rejected
    assert client.post(
        "/api/v1/notifications/rules",
        json={"channel": "email", "target": ""},
        headers=admin_headers,
    ).status_code == 422

    # delete
    assert client.delete(
        f"/api/v1/notifications/rules/{rule['id']}", headers=admin_headers
    ).status_code == 204


def test_crud_forbidden_for_non_admin(client, admin_headers):
    client.post(
        "/api/v1/auth/users",
        json={"email": "notify-viewer@example.com", "password": "viewer123", "role": "viewer"},
        headers=admin_headers,
    )
    token = client.post(
        "/api/v1/auth/login", json={"email": "notify-viewer@example.com", "password": "viewer123"}
    ).json()["access_token"]
    vh = {"Authorization": f"Bearer {token}"}
    # viewer can read
    assert client.get("/api/v1/notifications/rules", headers=vh).status_code == 200
    # but cannot create
    assert client.post(
        "/api/v1/notifications/rules",
        json={"channel": "slack", "target": "https://x"},
        headers=vh,
    ).status_code == 403
