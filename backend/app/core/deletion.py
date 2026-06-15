"""Deletion helpers for app metadata entities with cross-feature dependents."""

from sqlalchemy import or_, select
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
    db.query(models.NotificationRule).filter(
        models.NotificationRule.dataset_id == dataset_id
    ).delete(synchronize_session=False)
    db.query(models.SavedQuery).filter(models.SavedQuery.dataset_id == dataset_id).update(
        {models.SavedQuery.dataset_id: None}, synchronize_session=False
    )
    db.query(models.CheckRun).filter(models.CheckRun.dataset_id == dataset_id).delete(
        synchronize_session=False
    )
