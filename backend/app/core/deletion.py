"""Deletion helpers for app metadata entities with cross-feature dependents."""

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import models


def cleanup_dataset_dependents(db: Session, dataset_id: int) -> None:
    """Remove or detach rows that reference a dataset outside ORM cascades.

    Dataset owns checks/profiles/knowledge through SQLAlchemy relationships. Newer
    features added direct dataset foreign keys without those relationships, so
    delete those hard dependents explicitly before deleting the dataset row.
    """
    run_ids = select(models.CheckRun.id).where(models.CheckRun.dataset_id == dataset_id)
    exception_filter = or_(
        models.ExceptionRecord.dataset_id == dataset_id,
        models.ExceptionRecord.run_id.in_(run_ids),
        models.ExceptionRecord.last_run_id.in_(run_ids),
    )
    exception_ids = select(models.ExceptionRecord.id).where(exception_filter)

    db.query(models.ExceptionEvent).filter(
        models.ExceptionEvent.exception_id.in_(exception_ids)
    ).delete(synchronize_session=False)
    db.query(models.ExceptionRecord).filter(exception_filter).delete(synchronize_session=False)
    db.query(models.RcaSession).filter(
        or_(models.RcaSession.dataset_id == dataset_id, models.RcaSession.check_run_id.in_(run_ids))
    ).delete(synchronize_session=False)
    db.query(models.AdhocDashboard).filter(
        models.AdhocDashboard.dataset_id == dataset_id
    ).delete(synchronize_session=False)
    db.query(models.SchemaSnapshot).filter(
        models.SchemaSnapshot.dataset_id == dataset_id
    ).delete(synchronize_session=False)
    # SLAs scoped to this dataset or to any of its checks (#102) — drop evaluations first (FK).
    check_ids = select(models.Check.id).where(models.Check.dataset_id == dataset_id)
    sla_filter = or_(
        and_(models.SLADefinition.scope == "dataset", models.SLADefinition.scope_id == dataset_id),
        and_(models.SLADefinition.scope == "check", models.SLADefinition.scope_id.in_(check_ids)),
    )
    sla_ids = select(models.SLADefinition.id).where(sla_filter)
    db.query(models.SLAEvaluation).filter(models.SLAEvaluation.sla_id.in_(sla_ids)).delete(
        synchronize_session=False
    )
    db.query(models.SLADefinition).filter(sla_filter).delete(synchronize_session=False)
    db.query(models.NotificationRule).filter(
        models.NotificationRule.dataset_id == dataset_id
    ).delete(synchronize_session=False)
    db.query(models.SavedQuery).filter(models.SavedQuery.dataset_id == dataset_id).update(
        {models.SavedQuery.dataset_id: None}, synchronize_session=False
    )
    db.query(models.CheckRun).filter(models.CheckRun.dataset_id == dataset_id).delete(
        synchronize_session=False
    )
