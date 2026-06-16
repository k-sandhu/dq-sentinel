from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
from app.models import utcnow
from app.security import get_current_user

router = APIRouter(prefix="/scorecards", tags=["scorecards"])


@router.get("/history", response_model=schemas.ScorecardHistoryOut)
def history(
    grain: schemas.ScorecardGrain = Query("global"),
    key: str | None = None,
    days: int = Query(90, ge=1, le=366),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    """Sparse daily scorecard points, ordered oldest first.

    Snapshot rows are aggregate app metadata only. Missing dates are omitted so
    clients can render honest gaps without expensive recomputation.
    """
    history_key = key.strip() if key and key.strip() else None
    if grain == "global" and history_key is None:
        history_key = "global"
    cutoff = utcnow().date() - timedelta(days=days - 1)

    query = db.query(models.ScorecardSnapshot).filter(
        models.ScorecardSnapshot.grain == grain,
        models.ScorecardSnapshot.snapshot_date >= cutoff,
    )
    if history_key is not None:
        query = query.filter(models.ScorecardSnapshot.key == history_key)
    points = query.order_by(
        models.ScorecardSnapshot.snapshot_date.asc(),
        models.ScorecardSnapshot.key.asc(),
    ).all()

    return schemas.ScorecardHistoryOut(
        grain=grain,
        key=history_key,
        days=days,
        sparse=True,
        points=points,
    )
