from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
from app.llm.client import llm_enabled
from app.security import get_current_user, require_role

router = APIRouter(prefix="/rca", tags=["rca"])


def _rca_out(db: Session, s: models.RcaSession) -> schemas.RcaOut:
    out = schemas.RcaOut.model_validate(s)
    ds = db.get(models.Dataset, s.dataset_id)
    out.dataset_name = ds.table_name if ds else ""
    return out


@router.post("/start", response_model=schemas.RcaOut, status_code=202)
def start_rca(
    body: schemas.RcaStartIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    if not llm_enabled():
        raise HTTPException(
            503, "Root-cause analysis requires an LLM. Set ANTHROPIC_API_KEY and restart."
        )
    dataset_id = body.dataset_id
    if body.check_run_id:
        run = db.get(models.CheckRun, body.check_run_id)
        if run is None:
            raise HTTPException(404, "Check run not found")
        dataset_id = run.dataset_id
    if dataset_id is None:
        raise HTTPException(422, "Provide dataset_id or check_run_id")
    if db.get(models.Dataset, dataset_id) is None:
        raise HTTPException(404, "Dataset not found")
    if not body.check_run_id and not body.question.strip():
        raise HTTPException(422, "Ad-hoc RCA needs a question")

    session = models.RcaSession(
        dataset_id=dataset_id,
        check_run_id=body.check_run_id,
        question=body.question,
        status="running",
        created_by_id=user.id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    from app.llm.rca_agent import run_rca_session

    background.add_task(run_rca_session, session.id)
    return _rca_out(db, session)


@router.get("", response_model=list[schemas.RcaOut])
def list_sessions(
    dataset_id: int | None = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    q = db.query(models.RcaSession)
    if dataset_id is not None:
        q = q.filter(models.RcaSession.dataset_id == dataset_id)
    return [_rca_out(db, s) for s in q.order_by(models.RcaSession.id.desc()).limit(50).all()]


@router.get("/{session_id}", response_model=schemas.RcaOut)
def get_session(
    session_id: int, db: Session = Depends(get_db), _: models.User = Depends(get_current_user)
):
    s = db.get(models.RcaSession, session_id)
    if s is None:
        raise HTTPException(404, "RCA session not found")
    return _rca_out(db, s)
