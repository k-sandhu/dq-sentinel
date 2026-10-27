"""Admin-managed, scoped machine credentials."""

import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.audit import audit
from app.db import get_db
from app.security import require_role

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.post("", response_model=schemas.ApiTokenCreatedOut, status_code=201)
def create_token(
    body: schemas.ApiTokenIn,
    response: Response,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_role("admin")),
):
    if db.query(models.ApiToken).filter_by(name=body.name).first():
        raise HTTPException(409, "A token with this name already exists")
    scope = sorted(set(body.connection_scope))
    existing = {i for (i,) in db.query(models.Connection.id).filter(models.Connection.id.in_(scope))}
    missing = sorted(set(scope) - existing)
    if missing:
        raise HTTPException(422, f"Unknown connection ids: {missing}")
    response.headers["Cache-Control"] = "no-store"
    secret = "dqs_" + secrets.token_hex(16)
    token = models.ApiToken(
        name=body.name,
        token_hash=hashlib.sha256(secret.encode()).hexdigest(),
        role=body.role,
        connection_scope=scope,
        created_by_id=admin.id,
        expires_at=models.utcnow() + timedelta(days=body.expires_in_days),
    )
    db.add(token)
    db.flush()
    audit(
        db,
        admin,
        "token.create",
        "api_token",
        token.id,
        name=token.name,
        role=token.role,
        connection_scope=scope,
        expires_at=token.expires_at.isoformat(),
    )
    db.commit()
    return schemas.ApiTokenCreatedOut(**schemas.ApiTokenOut.model_validate(token).model_dump(), token=secret)


@router.get("", response_model=list[schemas.ApiTokenOut])
def list_tokens(db: Session = Depends(get_db), _: models.User = Depends(require_role("admin"))):
    return db.query(models.ApiToken).order_by(models.ApiToken.id.desc()).all()


@router.delete("/{token_id}", status_code=204)
def revoke_token(
    token_id: int, db: Session = Depends(get_db), admin: models.User = Depends(require_role("admin"))
):
    token = db.get(models.ApiToken, token_id)
    if token is None:
        raise HTTPException(404, "Token not found")
    if token.revoked_at is None:
        token.revoked_at = models.utcnow()
        audit(db, admin, "token.revoke", "api_token", token.id, name=token.name)
        db.commit()
    return Response(status_code=204)
