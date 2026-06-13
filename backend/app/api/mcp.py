"""Admin CRUD for MCP servers (code context for LLM agents). Tokens are
write-only: accepted on create/update, never returned."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.audit import audit
from app.db import get_db
from app.security import get_current_user, require_role

router = APIRouter(prefix="/mcp-servers", tags=["mcp"])


def _out(s: models.McpServer) -> schemas.McpServerOut:
    out = schemas.McpServerOut.model_validate(s)
    out.has_token = bool(s.auth_token)
    return out


@router.get("", response_model=list[schemas.McpServerOut])
def list_servers(db: Session = Depends(get_db), _: models.User = Depends(get_current_user)):
    return [_out(s) for s in db.query(models.McpServer).order_by(models.McpServer.name).all()]


@router.post("", response_model=schemas.McpServerOut, status_code=201)
def create_server(
    body: schemas.McpServerIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("admin")),
):
    if not body.url.startswith(("https://", "http://")):
        raise HTTPException(422, "URL must be http(s)")
    if db.query(models.McpServer).filter(models.McpServer.name == body.name).first():
        raise HTTPException(409, "An MCP server with this name already exists")
    server = models.McpServer(
        name=body.name,
        url=body.url,
        auth_token=body.auth_token,
        description=body.description,
        enabled=body.enabled,
    )
    db.add(server)
    db.flush()  # assign server.id for the audit row
    audit(db, user, "mcp.create", "mcp", server.id, name=server.name)  # never the token
    db.commit()
    db.refresh(server)
    return _out(server)


@router.patch("/{server_id}", response_model=schemas.McpServerOut)
def update_server(
    server_id: int,
    body: schemas.McpServerUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("admin")),
):
    server = db.get(models.McpServer, server_id)
    if server is None:
        raise HTTPException(404, "MCP server not found")
    data = body.model_dump(exclude_unset=True)
    for field in ("name", "url", "description", "enabled", "auth_token"):
        if field in data and data[field] is not None:
            setattr(server, field, data[field])
    # Record which fields changed by name only — never the token value.
    audit(db, user, "mcp.update", "mcp", server.id, fields=sorted(data.keys()))
    db.commit()
    db.refresh(server)
    return _out(server)


@router.delete("/{server_id}", status_code=204)
def delete_server(
    server_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("admin")),
):
    server = db.get(models.McpServer, server_id)
    if server is None:
        raise HTTPException(404, "MCP server not found")
    audit(db, user, "mcp.delete", "mcp", server.id, name=server.name)
    db.delete(server)
    db.commit()
