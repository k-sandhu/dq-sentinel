import json

import httpx
import pytest

from app.config import get_settings
from app.core import notify
from app.db import init_db, session_factory
from app.models import Check, CheckRun, Connection, Dataset, Incident, NotificationRule, utcnow


class FakeResponse:
    def __init__(self, data=None):
        self._data = data or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    init_db()
    with session_factory()() as db:
        from app.models import IncidentEvent

        db.query(IncidentEvent).delete()
        db.query(Incident).delete()
        db.query(NotificationRule).delete()
        db.commit()
    for name in (
        "DQ_WEBHOOK_HMAC_SECRET",
        "DQ_PAGERDUTY_ROUTING_KEY",
        "DQ_JIRA_BASE_URL",
        "DQ_JIRA_EMAIL",
        "DQ_JIRA_API_TOKEN",
        "DQ_JIRA_PROJECT_KEY",
        "DQ_SERVICENOW_INSTANCE_URL",
        "DQ_SERVICENOW_USER",
        "DQ_SERVICENOW_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


_seq = 0


def _incident_graph(db):
    global _seq
    _seq += 1
    conn = Connection(
        name=f"channel-src-{_seq}",
        kind="sqlite",
        dsn="sqlite:///unused.sqlite",
    )
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, table_name="people", display_name="people")
    db.add(ds)
    db.flush()
    check = Check(
        dataset_id=ds.id,
        name="email not null",
        check_type="not_null",
        column_name="email",
        severity="error",
        status="active",
    )
    db.add(check)
    db.flush()
    run = CheckRun(
        check_id=check.id,
        dataset_id=ds.id,
        status="fail",
        violation_count=5,
        rows_evaluated=200,
        finished_at=utcnow(),
    )
    db.add(run)
    db.flush()
    incident = Incident(
        dataset_id=ds.id,
        check_id=check.id,
        current_run_id=run.id,
        dedupe_key=f"check:{check.id}:failure",
        title="email not null failing",
        severity="error",
        status="open",
        failure_status="fail",
        occurrence_count=1,
        external_refs={},
    )
    db.add(incident)
    db.flush()
    return check, run, incident


def test_generic_webhook_payload_has_identifiers_and_no_row_data(monkeypatch):
    calls = []

    def fake_post(url, *, content=None, headers=None, timeout=None, **kwargs):
        calls.append({"url": url, "content": content, "headers": headers, "timeout": timeout, "kwargs": kwargs})
        return FakeResponse()

    monkeypatch.setenv("DQ_WEBHOOK_HMAC_SECRET", "secret")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "post", fake_post)

    with session_factory()() as db:
        check, run, incident = _incident_graph(db)
        event = notify._incident_event(db, incident, check, run, "triggered")

    notify.GenericWebhook("https://webhook.example/dq").send_incident(event)

    assert calls
    payload = json.loads(calls[0]["content"])
    assert payload["incident"]["id"] == incident.id
    assert payload["dataset"]["id"] == check.dataset_id
    assert payload["check"]["id"] == check.id
    assert payload["run"]["id"] == run.id
    assert "row_data" not in calls[0]["content"].decode()
    assert calls[0]["headers"]["X-DQ-Sentinel-Signature"].startswith("sha256=")


def test_pagerduty_trigger_and_resolve_use_same_dedupe_key(monkeypatch):
    calls = []

    def fake_post(url, *, json=None, timeout=None, **kwargs):
        calls.append({"url": url, "json": json, "timeout": timeout, "kwargs": kwargs})
        return FakeResponse({"dedup_key": json["dedup_key"]})

    monkeypatch.setenv("DQ_PAGERDUTY_ROUTING_KEY", "rk_test")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "post", fake_post)

    with session_factory()() as db:
        check, run, incident = _incident_graph(db)
        trigger = notify._incident_event(db, incident, check, run, "triggered")
        recovered = notify._incident_event(db, incident, check, run, "recovered")

    pd = notify.PagerDutyEvents()
    pd.send_incident(trigger)
    pd.send_incident(recovered)

    assert [c["json"]["event_action"] for c in calls] == ["trigger", "resolve"]
    assert calls[0]["json"]["dedup_key"] == calls[1]["json"]["dedup_key"]
    assert calls[0]["json"]["dedup_key"] == incident.dedupe_key


def test_missing_pagerduty_credentials_are_clear(client, admin_headers):
    resp = client.post(
        "/api/v1/notifications/rules",
        json={"channel": "pagerduty", "target": "", "min_severity": "error"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    rule_id = resp.json()["id"]

    test = client.post(f"/api/v1/notifications/rules/{rule_id}/test", headers=admin_headers)
    assert test.status_code == 200
    body = test.json()
    assert body["ok"] is False
    assert "DQ_PAGERDUTY_ROUTING_KEY" in body["message"]


def test_jira_external_refs_are_persisted(monkeypatch):
    calls = []

    def fake_post(url, *, json=None, auth=None, timeout=None, **kwargs):
        calls.append({"url": url, "json": json, "auth": auth, "timeout": timeout, "kwargs": kwargs})
        if url.endswith("/issue"):
            return FakeResponse({"key": "DQ-123", "self": "https://jira.example/rest/api/3/issue/10000"})
        return FakeResponse({})

    monkeypatch.setenv("DQ_JIRA_BASE_URL", "https://jira.example")
    monkeypatch.setenv("DQ_JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("DQ_JIRA_API_TOKEN", "token")
    monkeypatch.setenv("DQ_JIRA_PROJECT_KEY", "DQ")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "post", fake_post)

    with session_factory()() as db:
        check, run, incident = _incident_graph(db)
        db.add(NotificationRule(channel="jira", target="", min_severity="error", enabled=True))
        db.commit()
        notify.dispatch_incident(db, incident, check, run, "triggered")
        db.commit()
        db.refresh(incident)

    assert incident.external_refs["jira"]["key"] == "DQ-123"
    assert incident.external_refs["jira"]["url"] == "https://jira.example/browse/DQ-123"
    assert calls[0]["url"] == "https://jira.example/rest/api/3/issue"


def test_servicenow_external_refs_are_persisted(monkeypatch):
    calls = []

    def fake_post(url, *, json=None, auth=None, timeout=None, **kwargs):
        calls.append({"url": url, "json": json, "auth": auth, "timeout": timeout, "kwargs": kwargs})
        return FakeResponse({"result": {"sys_id": "abc123", "number": "INC0010001"}})

    monkeypatch.setenv("DQ_SERVICENOW_INSTANCE_URL", "https://snow.example")
    monkeypatch.setenv("DQ_SERVICENOW_USER", "svc")
    monkeypatch.setenv("DQ_SERVICENOW_PASSWORD", "pw")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "post", fake_post)

    with session_factory()() as db:
        check, run, incident = _incident_graph(db)
        db.add(NotificationRule(channel="servicenow", target="", min_severity="error", enabled=True))
        db.commit()
        notify.dispatch_incident(db, incident, check, run, "triggered")
        db.commit()
        db.refresh(incident)

    assert incident.external_refs["servicenow"]["sys_id"] == "abc123"
    assert incident.external_refs["servicenow"]["number"] == "INC0010001"
    assert calls[0]["url"] == "https://snow.example/api/now/table/incident"
