from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.audit import audit
from app.db import get_db
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.TokenOut)
def login(body: schemas.LoginIn, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == body.email.lower()).first()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        audit(db, None, "login.failure", "user", None, email=body.email.lower())
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    audit(db, user, "login.success", "user", user.id)
    db.commit()
    return schemas.TokenOut(access_token=create_access_token(user), user=user)


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user


@router.get("/users", response_model=list[schemas.UserOut])
def list_users(db: Session = Depends(get_db), _: models.User = Depends(require_role("admin"))):
    return db.query(models.User).order_by(models.User.id).all()


@router.get("/assignees", response_model=list[schemas.AssigneeOut])
def list_assignees(
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    """Active users for the triage assignee picker (#56). Any authenticated user
    (the admin-only /auth/users stays admin-gated); returns the minimum shape —
    no roles/is_active — so a non-admin dropdown doesn't leak authz state."""
    return (
        db.query(models.User)
        .filter(models.User.is_active.is_(True))
        .order_by(models.User.name, models.User.id)
        .all()
    )


@router.post("/users", response_model=schemas.UserOut, status_code=201)
def create_user(
    body: schemas.UserCreate,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_role("admin")),
):
    email = body.email.lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(409, "A user with this email already exists")
    user = models.User(
        email=email, name=body.name, password_hash=hash_password(body.password), role=body.role
    )
    db.add(user)
    db.flush()  # assign user.id for the audit row
    audit(db, admin, "user.create", "user", user.id, email=email, role=user.role)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: int,
    body: schemas.UserUpdate,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_role("admin")),
):
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    changed: list[str] = []
    if body.name is not None:
        user.name = body.name
        changed.append("name")
    if body.password:
        user.password_hash = hash_password(body.password)
        changed.append("password")  # field name only — NEVER the value/hash
    if body.role is not None:
        user.role = body.role
        changed.append("role")
    if body.is_active is not None:
        if user.id == admin.id and body.is_active is False:
            raise HTTPException(400, "You cannot deactivate your own account")
        user.is_active = body.is_active
        changed.append("is_active")
    audit(db, admin, "user.update", "user", user.id, changed=changed)
    db.commit()
    db.refresh(user)
    return user
