"""Append-only triage activity events for exceptions (#56).

A shared helper (rather than inline in the router) because the runner's
lifecycle flips from #55 — regression reopen and auto-resolve — also write
events as machine actions (`kind="system"`, `user_id=None`). Events are
write-once: there is intentionally no update/delete helper.
"""

from sqlalchemy.orm import Session

from app import models


def record_event(
    db: Session,
    exc: models.ExceptionRecord,
    kind: str,
    *,
    user_id: int | None = None,
    from_status: str = "",
    to_status: str = "",
    comment: str = "",
) -> models.ExceptionEvent:
    event = models.ExceptionEvent(
        exception_id=exc.id,
        user_id=user_id,
        kind=kind,
        from_status=from_status,
        to_status=to_status,
        comment=comment,
    )
    db.add(event)
    return event
