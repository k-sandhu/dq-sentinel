from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.audit import audit
from app.db import get_db
from app.security import get_current_user, require_role

router = APIRouter(prefix="/datasets/{dataset_id}/knowledge", tags=["knowledge"])


@router.get("", response_model=schemas.KnowledgeOut)
def get_knowledge(
    dataset_id: int, db: Session = Depends(get_db), _: models.User = Depends(get_current_user)
):
    ds = db.get(models.Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    k = ds.knowledge
    if k is None:
        return schemas.KnowledgeOut(dataset_id=dataset_id)
    return schemas.KnowledgeOut.model_validate(k)


@router.put("", response_model=schemas.KnowledgeOut)
def put_knowledge(
    dataset_id: int,
    body: schemas.KnowledgeIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    ds = db.get(models.Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    k = ds.knowledge
    if k is None:
        k = models.TableKnowledge(dataset_id=dataset_id)
        db.add(k)
    for field, value in body.model_dump().items():
        setattr(k, field, value)
    k.updated_by_id = user.id
    audit(db, user, "knowledge.update", "dataset", dataset_id, importance=k.importance)
    db.commit()
    db.refresh(k)
    return schemas.KnowledgeOut.model_validate(k)
