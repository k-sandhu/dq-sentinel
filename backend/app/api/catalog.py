"""Built-in data catalog API (curated, one-click enterprise datasets).

GET is open to any authenticated user; connecting requires editor (it creates a
connection + datasets + checks), and disconnecting requires admin (it deletes a
connection and its dependents), mirroring the role gates on those resources.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import models, schemas
from app.catalog import definitions, seed
from app.db import get_db
from app.security import get_current_user, require_role

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("", response_model=list[schemas.CatalogEntryOut])
def list_catalog(
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    """The full catalog with per-entry connected-state and a governance preview."""
    return [seed.entry_status(db, entry) for entry in definitions.CATALOG]


@router.post("/{key}/connect", response_model=schemas.CatalogEntryOut)
def connect_dataset(
    key: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    """Materialize the dataset: backing data, profile, knowledge, an active data
    contract, curated active checks, and SLAs. Idempotent."""
    entry = definitions.entry_by_key(key)
    if entry is None:
        raise HTTPException(404, f"Unknown catalog dataset '{key}'")
    try:
        seed.connect_entry(db, entry, user)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return seed.entry_status(db, entry)


@router.delete("/{key}/disconnect", status_code=204)
def disconnect_dataset(
    key: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("admin")),
):
    """Remove the connection and all of its dependents (no-op if not connected).
    The generated backing file is left in place for a fast re-connect."""
    entry = definitions.entry_by_key(key)
    if entry is None:
        raise HTTPException(404, f"Unknown catalog dataset '{key}'")
    seed.disconnect_entry(db, entry, user)
    return Response(status_code=204)
