from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
from app.security import get_current_user

router = APIRouter(prefix="/search", tags=["search"])

TYPE_ORDER = {
    "dataset": 0,
    "check": 1,
    "connection": 2,
    "saved_query": 3,
}


@dataclass(frozen=True)
class RankedHit:
    hit: schemas.SearchHit
    prefix_match: bool


def _dataset_title(dataset: models.Dataset) -> str:
    if dataset.schema_name:
        return f"{dataset.schema_name}.{dataset.table_name}"
    return dataset.display_name or dataset.table_name


def _dataset_label(dataset: models.Dataset) -> str:
    if dataset.schema_name:
        return f"{dataset.schema_name}.{dataset.table_name}"
    return dataset.table_name


def _has_prefix(needle: str, *values: str | None) -> bool:
    return any((value or "").lower().startswith(needle) for value in values)


def _dataset_hits(db: Session, needle: str, like: str, limit: int) -> list[RankedHit]:
    rows = (
        db.query(models.Dataset)
        .join(models.Connection)
        .filter(
            func.lower(models.Dataset.table_name).like(like)
            | func.lower(models.Dataset.display_name).like(like)
        )
        .order_by(models.Dataset.table_name)
        .limit(limit)
        .all()
    )
    return [
        RankedHit(
            hit=schemas.SearchHit(
                type="dataset",
                id=dataset.id,
                title=_dataset_title(dataset),
                subtitle=dataset.connection.name,
                url=f"/datasets/{dataset.id}",
            ),
            prefix_match=_has_prefix(
                needle, dataset.table_name, dataset.display_name, _dataset_title(dataset)
            ),
        )
        for dataset in rows
    ]


def _check_hits(db: Session, needle: str, like: str, limit: int) -> list[RankedHit]:
    rows = (
        db.query(models.Check)
        .join(models.Dataset)
        .filter(models.Check.status != "archived", func.lower(models.Check.name).like(like))
        .order_by(models.Check.name)
        .limit(limit)
        .all()
    )
    return [
        RankedHit(
            hit=schemas.SearchHit(
                type="check",
                id=check.id,
                title=check.name,
                subtitle=_dataset_label(check.dataset),
                url=f"/datasets/{check.dataset_id}/checks",
            ),
            prefix_match=_has_prefix(needle, check.name),
        )
        for check in rows
    ]


def _connection_hits(db: Session, needle: str, like: str, limit: int) -> list[RankedHit]:
    rows = (
        db.query(models.Connection)
        .filter(func.lower(models.Connection.name).like(like))
        .order_by(models.Connection.name)
        .limit(limit)
        .all()
    )
    return [
        RankedHit(
            hit=schemas.SearchHit(
                type="connection",
                id=connection.id,
                title=connection.name,
                subtitle=connection.kind,
                url="/connections",
            ),
            prefix_match=_has_prefix(needle, connection.name),
        )
        for connection in rows
    ]


def _saved_query_hits(db: Session, needle: str, like: str, limit: int) -> list[RankedHit]:
    saved_query_model = getattr(models, "SavedQuery", None)
    if (
        saved_query_model is None
        or not hasattr(saved_query_model, "id")
        or not hasattr(saved_query_model, "name")
    ):
        return []

    try:
        rows = (
            db.query(saved_query_model)
            .filter(func.lower(saved_query_model.name).like(like))
            .order_by(saved_query_model.name)
            .limit(limit)
            .all()
        )
    except SQLAlchemyError:
        return []

    hits: list[RankedHit] = []
    for saved_query in rows:
        name = getattr(saved_query, "name", "")
        saved_query_id = saved_query.id
        hits.append(
            RankedHit(
                hit=schemas.SearchHit(
                    type="saved_query",
                    id=saved_query_id,
                    title=name,
                    subtitle="Saved query",
                    url=f"/workbench?saved_query_id={saved_query_id}",
                ),
                prefix_match=_has_prefix(needle, name),
            )
        )
    return hits


@router.get("", response_model=schemas.SearchOut)
def global_search(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    needle = q.strip().lower()
    if not needle:
        return schemas.SearchOut(hits=[])

    like = f"%{needle}%"
    # TODO(#26): Filter metadata hits by per-connection grants once connection RBAC lands.
    ranked = [
        *_dataset_hits(db, needle, like, limit),
        *_check_hits(db, needle, like, limit),
        *_connection_hits(db, needle, like, limit),
        *_saved_query_hits(db, needle, like, limit),
    ]
    ranked.sort(
        key=lambda item: (
            not item.prefix_match,
            TYPE_ORDER[item.hit.type],
            item.hit.title.lower(),
            item.hit.id,
        )
    )
    return schemas.SearchOut(hits=[item.hit for item in ranked])
