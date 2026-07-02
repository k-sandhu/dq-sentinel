"""Append-only audit trail helper (issue #30).

``audit()`` stages an ``AuditEntry`` on the caller's session *without* committing:
it must land in the SAME transaction as the change it records, so the endpoint's
existing ``db.commit()`` persists both atomically (and a rollback drops both).

GOLDEN RULE: never pass secrets, DSNs, password hashes, or source row data in
``detail``. Pass names/kinds/diffs/counts only.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEntry


def audit(
    db: Session,
    user: Any,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    **detail: Any,
) -> None:
    """Stage one audit row. Caller commits (piggybacks on the endpoint's commit).

    ``user`` may be a ``User``, ``None`` (system/anonymous, e.g. a failed login),
    or anything with an ``id`` attribute. ``detail`` keyword args become the JSON
    ``detail`` payload.
    """
    from app.observability import client_ip_var, request_id_var

    rid = request_id_var.get()
    db.add(
        AuditEntry(
            user_id=getattr(user, "id", None),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
            request_id="" if rid == "-" else rid[:16],
            client_ip=client_ip_var.get()[:45],  # populated by the request middleware (#A15)
        )
    )
