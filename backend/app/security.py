"""Password hashing, JWT issuing/verification, and auth dependencies."""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Connection, ConnectionGrant, User

ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}

_bearer = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user: User) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "exp": datetime.now(UTC) + timedelta(hours=settings.access_token_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_role(min_role: str):
    """Dependency factory: require_role('editor') allows editor and admin."""

    def dep(user: User = Depends(get_current_user)) -> User:
        if ROLE_RANK.get(user.role, -1) < ROLE_RANK[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {min_role} role")
        return user

    return dep


# ---- per-connection authorization (#26 PR2 / #72 / #159) ----------------------


def visible_connection_ids(db: Session, user: User) -> set[int] | None:
    """Connection ids this user may see, or ``None`` for unrestricted.

    ``None`` -> apply no connection filter: a global ``admin``, OR a user with
    zero grants (legacy global-role behavior, backward compatible). A ``set`` ->
    restrict every connection-scoped query to exactly these ids.

    Per-request only — callers must NOT cache this across requests, so a grant
    change takes effect immediately (matching the role-check posture).
    """
    if user.role == "admin":
        return None
    ids = {
        cid
        for (cid,) in db.query(ConnectionGrant.connection_id)
        .filter(ConnectionGrant.user_id == user.id)
        .all()
    }
    return ids or None  # zero grants -> legacy full visibility


def connection_role(db: Session, user: User, connection_id: int) -> str | None:
    """Effective role on one connection, or ``None`` if it doesn't exist or the
    user can't see it.

    ``admin`` -> ``"admin"``; a zero-grant user -> their global role (legacy); a
    granted user -> the grant's role on that connection (``None`` if ungranted).
    """
    if db.get(Connection, connection_id) is None:
        return None  # nonexistent connection -> no role (keeps the by-id gate 404-consistent)
    if user.role == "admin":
        return "admin"
    grants = dict(
        db.query(ConnectionGrant.connection_id, ConnectionGrant.role)
        .filter(ConnectionGrant.user_id == user.id)
        .all()
    )
    if not grants:
        return user.role  # zero grants -> legacy global role
    return grants.get(connection_id)  # None -> ungranted -> no access


def assert_connection_visible(db: Session, user: User, connection_id: int) -> Connection:
    """Return the Connection if the user may see it, else 404 — the SAME status for
    a missing and an invisible connection so existence isn't leaked (#72)."""
    conn = db.get(Connection, connection_id)  # cached in the identity map for connection_role
    if conn is None or connection_role(db, user, connection_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return conn
