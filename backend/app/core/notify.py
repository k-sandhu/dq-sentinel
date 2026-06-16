"""Notification channels + rule dispatch for failed/recovered check runs (issue #27).

Two channels — Slack incoming-webhook (via httpx, already a dependency) and SMTP
email (stdlib ``smtplib`` + ``email.message``) — behind a tiny ``Channel`` Protocol
so the digest feature (#42) can reuse them. ``dispatch``/``dispatch_recovery`` are
called from ``core.runner.run_check`` AFTER commit, best-effort: the transition
decision (when to fire) lives there; this module decides *where* a fired event goes
(rule matching + severity gate) and how to deliver it.

Every send is wrapped so a dead webhook or SMTP host can NEVER fail a check run —
failures are logged at WARNING and counted in ``dq_notifications_sent_total``.
With no settings and no rules there are zero sends and zero behaviour change.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Check, CheckRun, Dataset, NotificationRule
from app.observability import NOTIFICATIONS_SENT

log = logging.getLogger(__name__)

# Severity ordering for the rule gate (mirrors security.ROLE_RANK style).
SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2}

# Network/SMTP timeout — keep short so a hung host can't stall a worker thread.
_SEND_TIMEOUT = 10.0


class Channel(Protocol):
    def send(self, subject: str, body: str, link: str | None) -> None: ...


class SlackWebhook:
    """Post to a Slack incoming webhook. ``url`` falls back to the global
    ``notify_slack_webhook_url`` setting when blank."""

    def __init__(self, url: str) -> None:
        self.url = url

    def send(self, subject: str, body: str, link: str | None) -> None:
        text = f"*{subject}*\n{body}"
        if link:
            text += f"\n{link}"
        resp = httpx.post(self.url, json={"text": text}, timeout=_SEND_TIMEOUT)
        resp.raise_for_status()


class SmtpEmail:
    """Send a plaintext email via stdlib smtplib (STARTTLS when configured).

    ``recipients`` is a list of addresses; transport (host/port/auth/from) comes
    from settings. Raises if SMTP is not configured so the caller logs + counts a
    failure rather than silently dropping the alert."""

    def __init__(self, recipients: list[str]) -> None:
        self.recipients = recipients

    def send(self, subject: str, body: str, link: str | None) -> None:
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


def _channel_for(rule: NotificationRule) -> Channel | None:
    """Build the transport for a rule, or None when it can't be delivered
    (e.g. a Slack rule with no target and no global webhook configured)."""
    if rule.channel == "slack":
        url = rule.target.strip() or get_settings().notify_slack_webhook_url.strip()
        if not url:
            return None
        return SlackWebhook(url)
    if rule.channel == "email":
        recipients = [a.strip() for a in rule.target.split(",") if a.strip()]
        if not recipients:
            return None
        return SmtpEmail(recipients)
    return None


def _matching_rules(db: Session, check: Check) -> list[NotificationRule]:
    """Enabled rules whose dataset + severity gate the check passes.

    A NULL ``dataset_id`` means "all datasets" — and SQL ``IN (NULL, ...)`` never
    matches NULL rows, so the global case needs an explicit ``IS NULL`` branch."""
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


def _dataset_label(db: Session, check: Check) -> str:
    ds = check.dataset or db.get(Dataset, check.dataset_id)
    if ds is None:
        return f"dataset {check.dataset_id}"
    return ds.display_name or ds.table_name or f"dataset {ds.id}"


def _deliver(channel_kind: str, channel: Channel, subject: str, body: str, link: str | None) -> None:
    """Send through one channel, counting the outcome. A failed send is logged
    at WARNING and swallowed — it must never propagate into a check run."""
    try:
        channel.send(subject, body, link)
        NOTIFICATIONS_SENT.labels(channel_kind, "success").inc()
    except Exception as exc:  # noqa: BLE001 - a dead channel must not fail a run
        NOTIFICATIONS_SENT.labels(channel_kind, "failure").inc()
        log.warning(
            "notification send failed",
            extra={"event": "notify_failed", "channel": channel_kind},
            exc_info=exc,
        )


def _dispatch(db: Session, check: Check, run: CheckRun, *, subject: str, body_prefix: str) -> None:
    """Shared rule-match + per-channel deliver for both failure and recovery."""
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

    for rule in _matching_rules(db, check):
        # Infra failures (status == "error") only go to rules that opted in.
        if run.status == "error" and not rule.on_error_runs:
            continue
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
    """A check that was passing is now failing/erroring — notify matching rules."""
    dataset_label = _dataset_label(db, check)
    subject = f"[DQ] {check.name} failing on {dataset_label}"
    _dispatch(db, check, run, subject=subject, body_prefix=f"Check '{check.name}' is now {run.status}.")


def dispatch_recovery(db: Session, check: Check, run: CheckRun) -> None:
    """A previously failing check passed again — notify so the loop is closed."""
    dataset_label = _dataset_label(db, check)
    subject = f"[DQ] {check.name} recovered on {dataset_label}"
    _dispatch(db, check, run, subject=subject, body_prefix=f"Check '{check.name}' has recovered (now passing).")


def dispatch_sla_breach(db: Session, sla, evaluation) -> None:
    """An SLA newly dropped below its objective — notify matching rules (#102).

    Routes by the SLA's dataset (check-scoped SLAs resolve to their check's
    dataset). A breach is error-level, so every enabled rule whose dataset gate
    matches qualifies. Best-effort like the check paths.
    """
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
