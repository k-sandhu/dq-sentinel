"""Notification channels + rule dispatch for check incidents and recoveries.

Delivery is best-effort by design: every channel send is isolated so a dead
webhook, SMTP host, or ticketing API can never fail a check run or incident
persistence. The incident service decides *when* to dispatch; this module
decides *where* and *how*.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Check, CheckRun, Dataset, Incident, NotificationRule
from app.observability import NOTIFICATIONS_SENT

log = logging.getLogger(__name__)

# Severity ordering for the rule gate (mirrors security.ROLE_RANK style).
SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2}

# Network/SMTP timeout: short enough that a hung downstream cannot stall a worker.
_SEND_TIMEOUT = 10.0


@dataclass(frozen=True)
class IncidentNotification:
    """Channel-neutral incident payload.

    This intentionally contains identifiers and aggregates only. Source row
    samples stay on exception APIs and are never copied into notification bodies.
    """

    action: str  # triggered | recovered | escalated | test
    incident_id: int | None
    incident_status: str
    dedupe_key: str
    title: str
    severity: str
    occurrence_count: int
    escalation_level: int
    dataset_id: int | None
    dataset_name: str
    check_id: int | None
    check_name: str
    check_type: str
    run_id: int | None
    run_status: str
    failure_status: str
    violation_count: int
    rows_evaluated: int | None
    error_message: str
    link: str | None
    external_refs: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        return {
            "status": self.action,
            "incident": {
                "id": self.incident_id,
                "status": self.incident_status,
                "dedupe_key": self.dedupe_key,
                "title": self.title,
                "severity": self.severity,
                "occurrence_count": self.occurrence_count,
                "escalation_level": self.escalation_level,
                "external_refs": self.external_refs,
            },
            "dataset": {"id": self.dataset_id, "name": self.dataset_name},
            "check": {"id": self.check_id, "name": self.check_name, "type": self.check_type},
            "run": {
                "id": self.run_id,
                "status": self.run_status,
                "failure_status": self.failure_status,
                "violation_count": self.violation_count,
                "rows_evaluated": self.rows_evaluated,
                "error_message": self.error_message,
            },
            "links": {"incident": self.link},
        }


class Channel(Protocol):
    def send(self, subject: str, body: str, link: str | None) -> dict[str, Any] | None: ...


class SlackWebhook:
    """Post to a Slack incoming webhook.

    ``url`` falls back to the global ``notify_slack_webhook_url`` setting when a
    Slack rule leaves target blank, preserving the existing issue #27 behavior.
    """

    def __init__(self, url: str) -> None:
        self.url = url

    def send(self, subject: str, body: str, link: str | None) -> dict[str, Any] | None:
        text = f"*{subject}*\n{body}"
        if link:
            text += f"\n{link}"
        resp = httpx.post(self.url, json={"text": text}, timeout=_SEND_TIMEOUT)
        resp.raise_for_status()
        return None


class SmtpEmail:
    """Send a plaintext email via stdlib smtplib."""

    def __init__(self, recipients: list[str]) -> None:
        self.recipients = recipients

    def send(self, subject: str, body: str, link: str | None) -> dict[str, Any] | None:
        s = get_settings()
        if not s.smtp_host:
            raise RuntimeError("SMTP not configured (DQ_SMTP_HOST unset)")
        if not self.recipients:
            raise RuntimeError("email rule has no recipients")
        from_addr = s.smtp_from_addr or s.smtp_user or "dq-sentinel@localhost"

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(f"{body}\n\n{link}" if link else body)

        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=_SEND_TIMEOUT) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return None


class GenericWebhook:
    """POST structured incident JSON to a generic webhook."""

    def __init__(self, url: str) -> None:
        self.url = url

    def send(self, subject: str, body: str, link: str | None) -> dict[str, Any] | None:
        payload = {"subject": subject, "body": body, "link": link}
        self._post(payload)
        return None

    def send_incident(self, event: IncidentNotification) -> dict[str, Any] | None:
        self._post(event.payload())
        return None

    def _post(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, sort_keys=True, default=str).encode()
        headers = {"Content-Type": "application/json"}
        secret = get_settings().webhook_hmac_secret
        if secret:
            sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            headers["X-DQ-Sentinel-Signature"] = f"sha256={sig}"
        resp = httpx.post(self.url, content=raw, headers=headers, timeout=_SEND_TIMEOUT)
        resp.raise_for_status()


class TeamsWebhook:
    """Post a MessageCard-compatible payload to Microsoft Teams."""

    def __init__(self, url: str) -> None:
        self.url = url

    def send(self, subject: str, body: str, link: str | None) -> dict[str, Any] | None:
        self._post(subject, body, link)
        return None

    def send_incident(self, event: IncidentNotification) -> dict[str, Any] | None:
        payload = event.payload()
        facts = [
            {"name": "Dataset", "value": str(payload["dataset"]["name"] or payload["dataset"]["id"])},
            {"name": "Check", "value": str(payload["check"]["name"] or payload["check"]["id"])},
            {"name": "Status", "value": event.action},
            {"name": "Occurrences", "value": str(event.occurrence_count)},
        ]
        body = json.dumps(payload["run"], default=str)
        self._post(event.title, body, event.link, facts=facts)
        return None

    def _post(
        self,
        subject: str,
        body: str,
        link: str | None,
        *,
        facts: list[dict[str, str]] | None = None,
    ) -> None:
        card: dict[str, Any] = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": subject,
            "themeColor": "d13438",
            "title": subject,
            "text": body,
        }
        if facts:
            card["sections"] = [{"facts": facts}]
        if link:
            card["potentialAction"] = [
                {"@type": "OpenUri", "name": "Open in DQ Sentinel", "targets": [{"os": "default", "uri": link}]}
            ]
        resp = httpx.post(self.url, json=card, timeout=_SEND_TIMEOUT)
        resp.raise_for_status()


class PagerDutyEvents:
    """PagerDuty Events API v2 with stable incident dedupe keys."""

    def __init__(self, routing_key: str = "") -> None:
        self.routing_key = routing_key

    def send(self, subject: str, body: str, link: str | None) -> dict[str, Any] | None:
        event = IncidentNotification(
            action="triggered",
            incident_id=None,
            incident_status="open",
            dedupe_key="dqsentinel-test",
            title=subject,
            severity="error",
            occurrence_count=1,
            escalation_level=0,
            dataset_id=None,
            dataset_name="",
            check_id=None,
            check_name=subject,
            check_type="test",
            run_id=None,
            run_status="fail",
            failure_status="fail",
            violation_count=0,
            rows_evaluated=None,
            error_message=body,
            link=link,
            external_refs={},
        )
        return self.send_incident(event)

    def send_incident(self, event: IncidentNotification) -> dict[str, Any] | None:
        routing_key = self.routing_key or get_settings().pagerduty_routing_key
        if not routing_key:
            raise RuntimeError("PagerDuty not configured (DQ_PAGERDUTY_ROUTING_KEY unset)")
        event_action = "resolve" if event.action == "recovered" else "trigger"
        body: dict[str, Any] = {
            "routing_key": routing_key,
            "event_action": event_action,
            "dedup_key": event.dedupe_key,
        }
        if event_action == "trigger":
            body["payload"] = {
                "summary": event.title,
                "source": event.dataset_name or "DQ Sentinel",
                "severity": _pagerduty_severity(event.severity),
                "custom_details": event.payload(),
            }
            if event.link:
                body["links"] = [{"href": event.link, "text": "Open in DQ Sentinel"}]
        resp = httpx.post("https://events.pagerduty.com/v2/enqueue", json=body, timeout=_SEND_TIMEOUT)
        resp.raise_for_status()
        data = _safe_json(resp)
        return {"pagerduty": {"dedup_key": data.get("dedup_key") or event.dedupe_key}}


class JiraChannel:
    """Jira Cloud issue creation and comments.

    Credentials and default project/type come from settings. A rule target may
    optionally contain a non-secret project key override.
    """

    def __init__(self, project_key: str = "") -> None:
        self.project_key = project_key.strip()

    def send(self, subject: str, body: str, link: str | None) -> dict[str, Any] | None:
        event = _test_event("jira-test", subject, body, link)
        return self.send_incident(event)

    def send_incident(self, event: IncidentNotification) -> dict[str, Any] | None:
        s = get_settings()
        _require_jira(s)
        project_key = self.project_key or s.jira_project_key
        if not project_key:
            raise RuntimeError("Jira not configured (DQ_JIRA_PROJECT_KEY unset)")
        refs = dict((event.external_refs or {}).get("jira") or {})
        issue_key = refs.get("key")
        if not issue_key and event.action == "recovered":
            return None
        if not issue_key:
            payload = {
                "fields": {
                    "project": {"key": project_key},
                    "summary": event.title[:255],
                    "description": _jira_doc(_incident_text(event)),
                    "issuetype": {"name": s.jira_issue_type or "Bug"},
                    "labels": ["dq-sentinel"],
                }
            }
            resp = httpx.post(
                f"{s.jira_base_url.rstrip('/')}/rest/api/3/issue",
                json=payload,
                auth=httpx.BasicAuth(s.jira_email, s.jira_api_token),
                timeout=_SEND_TIMEOUT,
            )
            resp.raise_for_status()
            data = _safe_json(resp)
            issue_key = data.get("key")
            refs = {
                "key": issue_key,
                "url": f"{s.jira_base_url.rstrip('/')}/browse/{issue_key}" if issue_key else data.get("self", ""),
            }
            return {"jira": refs}

        self._add_comment(issue_key, _incident_text(event))
        return None

    def _add_comment(self, issue_key: str, body: str) -> None:
        s = get_settings()
        resp = httpx.post(
            f"{s.jira_base_url.rstrip('/')}/rest/api/3/issue/{issue_key}/comment",
            json={"body": _jira_doc(body)},
            auth=httpx.BasicAuth(s.jira_email, s.jira_api_token),
            timeout=_SEND_TIMEOUT,
        )
        resp.raise_for_status()


class ServiceNowChannel:
    """ServiceNow incident creation and work-note updates."""

    def __init__(self, assignment_group: str = "") -> None:
        self.assignment_group = assignment_group.strip()

    def send(self, subject: str, body: str, link: str | None) -> dict[str, Any] | None:
        event = _test_event("servicenow-test", subject, body, link)
        return self.send_incident(event)

    def send_incident(self, event: IncidentNotification) -> dict[str, Any] | None:
        s = get_settings()
        _require_servicenow(s)
        refs = dict((event.external_refs or {}).get("servicenow") or {})
        sys_id = refs.get("sys_id")
        if not sys_id and event.action == "recovered":
            return None
        if not sys_id:
            payload = {
                "short_description": event.title,
                "description": _incident_text(event),
                "category": "data quality",
                "urgency": "2" if event.severity == "error" else "3",
                "impact": "2" if event.severity == "error" else "3",
            }
            group = self.assignment_group or s.servicenow_assignment_group
            if group:
                payload["assignment_group"] = group
            resp = httpx.post(
                f"{s.servicenow_instance_url.rstrip('/')}/api/now/table/incident",
                json=payload,
                auth=httpx.BasicAuth(s.servicenow_user, s.servicenow_password),
                timeout=_SEND_TIMEOUT,
            )
            resp.raise_for_status()
            result = (_safe_json(resp).get("result") or {})
            sys_id = result.get("sys_id")
            number = result.get("number", "")
            refs = {
                "sys_id": sys_id,
                "number": number,
                "url": f"{s.servicenow_instance_url.rstrip('/')}/nav_to.do?uri=incident.do?sys_id={sys_id}"
                if sys_id
                else "",
            }
            return {"servicenow": refs}

        payload: dict[str, Any] = {"work_notes": _incident_text(event)}
        if event.action == "recovered":
            payload.update({"state": "6", "close_notes": "DQ Sentinel check recovered."})
        resp = httpx.patch(
            f"{s.servicenow_instance_url.rstrip('/')}/api/now/table/incident/{sys_id}",
            json=payload,
            auth=httpx.BasicAuth(s.servicenow_user, s.servicenow_password),
            timeout=_SEND_TIMEOUT,
        )
        resp.raise_for_status()
        return None


def _channel_for(rule: NotificationRule) -> Channel | None:
    """Build the transport for a rule, or None when it lacks a deliverable target."""
    s = get_settings()
    if rule.channel == "slack":
        url = rule.target.strip() or s.notify_slack_webhook_url.strip()
        return SlackWebhook(url) if url else None
    if rule.channel == "email":
        recipients = [a.strip() for a in rule.target.split(",") if a.strip()]
        return SmtpEmail(recipients) if recipients else None
    if rule.channel == "webhook":
        url = rule.target.strip() or s.webhook_url.strip()
        return GenericWebhook(url) if url else None
    if rule.channel == "teams":
        url = rule.target.strip() or s.teams_webhook_url.strip()
        return TeamsWebhook(url) if url else None
    if rule.channel == "pagerduty":
        return PagerDutyEvents()
    if rule.channel == "jira":
        return JiraChannel(rule.target)
    if rule.channel == "servicenow":
        return ServiceNowChannel(rule.target)
    return None


def _matching_rules(db: Session, check: Check) -> list[NotificationRule]:
    """Enabled rules whose dataset + severity gate the check passes."""
    sev = SEVERITY_RANK.get(check.severity, 0)
    rules = (
        db.query(NotificationRule)
        .filter(
            NotificationRule.enabled.is_(True),
            or_(
                NotificationRule.dataset_id.is_(None),
                NotificationRule.dataset_id == check.dataset_id,
            ),
        )
        .all()
    )
    return [r for r in rules if SEVERITY_RANK.get(r.min_severity, 0) <= sev]


def incident_rules(db: Session, check: Check, run: CheckRun | None = None) -> list[NotificationRule]:
    """Rules eligible for this incident event, including error-run opt-in."""
    rules = _matching_rules(db, check)
    if run is not None and run.status == "error":
        rules = [r for r in rules if r.on_error_runs]
    return rules


def _dataset_label(db: Session, check: Check) -> str:
    ds = check.dataset or db.get(Dataset, check.dataset_id)
    if ds is None:
        return f"dataset {check.dataset_id}"
    return ds.display_name or ds.table_name or f"dataset {ds.id}"


def _merge_external_refs(incident: Incident, refs: dict[str, Any] | None) -> None:
    if not refs:
        return
    merged = dict(incident.external_refs or {})
    for key, value in refs.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    incident.external_refs = merged


def _deliver(
    channel_kind: str,
    channel: Channel,
    subject: str,
    body: str,
    link: str | None,
    *,
    event: IncidentNotification | None = None,
) -> dict[str, Any] | None:
    """Send through one channel and count the outcome."""
    try:
        if event is not None and hasattr(channel, "send_incident"):
            refs = channel.send_incident(event)  # type: ignore[attr-defined]
        else:
            refs = channel.send(subject, body, link)
        NOTIFICATIONS_SENT.labels(channel_kind, "success").inc()
        return refs
    except Exception as exc:  # noqa: BLE001 - a dead channel must not fail a run
        NOTIFICATIONS_SENT.labels(channel_kind, "failure").inc()
        log.warning(
            "notification send failed",
            extra={"event": "notify_failed", "channel": channel_kind},
            exc_info=exc,
        )
        return None


def _dispatch(db: Session, check: Check, run: CheckRun, *, subject: str, body_prefix: str) -> None:
    """Shared legacy rule-match + per-channel deliver for SLA and compatibility paths."""
    link = f"{get_settings().base_url.rstrip('/')}/datasets/{check.dataset_id}/exceptions"
    body_lines = [body_prefix]
    if run.rows_evaluated is not None:
        body_lines.append(f"Rows evaluated: {run.rows_evaluated}")
    body_lines.append(f"Violations: {run.violation_count}")
    if run.status == "error" and run.error_message:
        body_lines.append(f"Error: {run.error_message}")
    sample = (run.metrics or {}).get("detail")
    if sample:
        body_lines.append(f"Detail: {sample}")
    body = "\n".join(body_lines)

    for rule in incident_rules(db, check, run):
        channel = _channel_for(rule)
        if channel is None:
            log.warning(
                "notification rule %s has no deliverable target",
                rule.id,
                extra={"event": "notify_skipped", "channel": rule.channel},
            )
            continue
        _deliver(rule.channel, channel, subject, body, link)


def dispatch(db: Session, check: Check, run: CheckRun) -> None:
    """Compatibility path: a check that was passing is now failing/erroring."""
    dataset_label = _dataset_label(db, check)
    subject = f"[DQ] {check.name} failing on {dataset_label}"
    _dispatch(db, check, run, subject=subject, body_prefix=f"Check '{check.name}' is now {run.status}.")


def dispatch_recovery(db: Session, check: Check, run: CheckRun) -> None:
    """Compatibility path: a previously failing check passed again."""
    dataset_label = _dataset_label(db, check)
    subject = f"[DQ] {check.name} recovered on {dataset_label}"
    _dispatch(db, check, run, subject=subject, body_prefix=f"Check '{check.name}' has recovered (now passing).")


def dispatch_incident(db: Session, incident: Incident, check: Check, run: CheckRun, action: str) -> int:
    """Incident-aware dispatch used by the lifecycle service.

    Ticketing transports may return external refs; those refs are merged onto the
    incident but not committed here so the caller controls the transaction.
    Returns the count of channel deliveries attempted, including failed sends.
    """
    event = _incident_event(db, incident, check, run, action)
    subject = _subject_for(event)
    body = _incident_text(event)
    attempted = 0
    for rule in incident_rules(db, check, run):
        channel = _channel_for(rule)
        if channel is None:
            log.warning(
                "notification rule %s has no deliverable target",
                rule.id,
                extra={"event": "notify_skipped", "channel": rule.channel},
            )
            continue
        attempted += 1
        refs = _deliver(rule.channel, channel, subject, body, event.link, event=event)
        _merge_external_refs(incident, refs)
    return attempted


def dispatch_sla_breach(db: Session, sla, evaluation) -> None:
    """An SLA newly dropped below its objective: notify matching rules (#102)."""
    from app.models import Check as _Check

    dataset_id = sla.scope_id
    if sla.scope == "check":
        chk = db.get(_Check, sla.scope_id)
        dataset_id = chk.dataset_id if chk else None

    rules = (
        db.query(NotificationRule)
        .filter(
            NotificationRule.enabled.is_(True),
            or_(NotificationRule.dataset_id.is_(None), NotificationRule.dataset_id == dataset_id),
        )
        .all()
    )
    if not rules:
        return
    pct, obj = round(evaluation.attainment * 100, 2), round(sla.objective * 100, 2)
    subject = f"[DQ] SLA breached: {sla.name}"
    body = (
        f"SLA '{sla.name}' attainment {pct}% is below its {obj}% objective over {sla.window} "
        f"({evaluation.bad} bad / {evaluation.good + evaluation.bad} runs)."
    )
    link = f"{get_settings().base_url.rstrip('/')}/reliability"
    for rule in rules:
        channel = _channel_for(rule)
        if channel is not None:
            _deliver(rule.channel, channel, subject, body, link)


def _incident_event(
    db: Session, incident: Incident, check: Check, run: CheckRun, action: str
) -> IncidentNotification:
    dataset = check.dataset or db.get(Dataset, check.dataset_id)
    dataset_name = dataset.display_name or dataset.table_name if dataset else f"dataset {check.dataset_id}"
    link = f"{get_settings().base_url.rstrip('/')}/datasets/{check.dataset_id}/exceptions"
    return IncidentNotification(
        action=action,
        incident_id=incident.id,
        incident_status=incident.status,
        dedupe_key=incident.dedupe_key,
        title=incident.title,
        severity=incident.severity,
        occurrence_count=incident.occurrence_count,
        escalation_level=incident.escalation_level,
        dataset_id=check.dataset_id,
        dataset_name=dataset_name,
        check_id=check.id,
        check_name=check.name,
        check_type=check.check_type,
        run_id=run.id,
        run_status=run.status,
        failure_status=incident.failure_status,
        violation_count=run.violation_count,
        rows_evaluated=run.rows_evaluated,
        error_message=run.error_message,
        link=link,
        external_refs=dict(incident.external_refs or {}),
    )


def _test_event(dedupe_key: str, subject: str, body: str, link: str | None) -> IncidentNotification:
    return IncidentNotification(
        action="test",
        incident_id=None,
        incident_status="open",
        dedupe_key=dedupe_key,
        title=subject,
        severity="error",
        occurrence_count=1,
        escalation_level=0,
        dataset_id=None,
        dataset_name="DQ Sentinel",
        check_id=None,
        check_name=subject,
        check_type="test",
        run_id=None,
        run_status="fail",
        failure_status="fail",
        violation_count=0,
        rows_evaluated=None,
        error_message=body,
        link=link,
        external_refs={},
    )


def _subject_for(event: IncidentNotification) -> str:
    if event.action == "recovered":
        return f"[DQ] {event.check_name} recovered on {event.dataset_name}"
    if event.action == "escalated":
        return f"[DQ] Escalation {event.escalation_level}: {event.check_name} on {event.dataset_name}"
    return f"[DQ] {event.check_name} failing on {event.dataset_name}"


def _incident_text(event: IncidentNotification) -> str:
    lines = [
        f"Incident: {event.title}",
        f"Status: {event.action}",
        f"Severity: {event.severity}",
        f"Dataset: {event.dataset_name} ({event.dataset_id})",
        f"Check: {event.check_name} ({event.check_id})",
        f"Run: {event.run_id} status={event.run_status}",
        f"Occurrences: {event.occurrence_count}",
        f"Violations: {event.violation_count}",
    ]
    if event.rows_evaluated is not None:
        lines.append(f"Rows evaluated: {event.rows_evaluated}")
    if event.error_message:
        lines.append(f"Error: {event.error_message}")
    if event.link:
        lines.append(f"Link: {event.link}")
    return "\n".join(lines)


def _pagerduty_severity(severity: str) -> str:
    return {"info": "info", "warn": "warning", "error": "error"}.get(severity, "error")


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _require_jira(settings) -> None:
    missing = []
    if not settings.jira_base_url:
        missing.append("DQ_JIRA_BASE_URL")
    if not settings.jira_email:
        missing.append("DQ_JIRA_EMAIL")
    if not settings.jira_api_token:
        missing.append("DQ_JIRA_API_TOKEN")
    if missing:
        raise RuntimeError(f"Jira not configured ({', '.join(missing)} unset)")


def _require_servicenow(settings) -> None:
    missing = []
    if not settings.servicenow_instance_url:
        missing.append("DQ_SERVICENOW_INSTANCE_URL")
    if not settings.servicenow_user:
        missing.append("DQ_SERVICENOW_USER")
    if not settings.servicenow_password:
        missing.append("DQ_SERVICENOW_PASSWORD")
    if missing:
        raise RuntimeError(f"ServiceNow not configured ({', '.join(missing)} unset)")


def _jira_doc(text: str) -> dict[str, Any]:
    """Minimal Atlassian document format document."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text[:30_000]}],
            }
        ],
    }
