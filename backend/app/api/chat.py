"""Assistant chat: session CRUD + the WebSocket that streams agent turns.

WS protocol (JSON frames):
  client -> server: {"type": "user_message", "content": str} | {"type": "stop"}
  server -> client: {"type": "session", ...}        on connect (incl. messages)
                    {"type": "message_saved", ...}   persisted user message
                    {"type": "status", "state": "thinking"|"tool", ...}
                    {"type": "step", "step": {...}}  sql / result / chart / text
                    {"type": "assistant_message", ...} persisted final message
                    {"type": "error", "detail": str}
                    {"type": "done"}                 turn finished

Browsers cannot set Authorization headers on WebSockets, so the JWT is passed
as a `token` query parameter and verified before any data flows. Close codes:
4401 bad/missing token, 4403 insufficient role, 4404 unknown session.
"""

import asyncio
import logging
import threading

import jwt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import get_settings
from app.db import get_db, session_factory
from app.llm.chat_agent import drop_history, run_chat_turn
from app.security import ROLE_RANK, get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


# --------------------------------------------------------------- REST
def _session_out(s: models.ChatSession) -> schemas.ChatSessionOut:
    out = schemas.ChatSessionOut.model_validate(s)
    out.message_count = len(s.messages)
    return out


def _owned_session(db: Session, session_id: int, user: models.User) -> models.ChatSession:
    s = db.get(models.ChatSession, session_id)
    if s is None:
        raise HTTPException(404, "Chat session not found")
    if s.created_by_id != user.id and user.role != "admin":
        raise HTTPException(403, "Not your chat session")
    return s


@router.get("/sessions", response_model=list[schemas.ChatSessionOut])
def list_sessions(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    q = db.query(models.ChatSession).filter(models.ChatSession.created_by_id == user.id)
    return [_session_out(s) for s in q.order_by(models.ChatSession.updated_at.desc()).limit(50).all()]


@router.post("/sessions", response_model=schemas.ChatSessionOut, status_code=201)
def create_session(
    body: schemas.ChatSessionCreateIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    s = models.ChatSession(title=body.title.strip(), created_by_id=user.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _session_out(s)


@router.get("/sessions/{session_id}", response_model=schemas.ChatSessionDetailOut)
def get_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    s = _owned_session(db, session_id, user)
    out = schemas.ChatSessionDetailOut.model_validate(s)
    out.message_count = len(s.messages)
    out.messages = [schemas.ChatMessageOut.model_validate(m) for m in s.messages]
    return out


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    s = _owned_session(db, session_id, user)
    db.delete(s)
    db.commit()
    drop_history(session_id)


# ----------------------------------------------------------- WebSocket
def _ws_user(token: str) -> models.User | None:
    """Resolve the JWT passed as a query parameter (no HTTPBearer on WS)."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    with session_factory()() as db:
        user = db.get(models.User, int(payload["sub"]))
        if user is None or not user.is_active:
            return None
        return user


@router.websocket("/ws/{session_id}")
async def chat_ws(websocket: WebSocket, session_id: int, token: str = ""):
    await websocket.accept()
    user = _ws_user(token)
    if user is None:
        await websocket.close(code=4401, reason="Not authenticated")
        return
    # The assistant executes (guarded, read-only) SQL against sources — same
    # privilege bar as the workbench.
    if ROLE_RANK.get(user.role, -1) < ROLE_RANK["editor"]:
        await websocket.close(code=4403, reason="Requires editor role")
        return

    with session_factory()() as db:
        chat = db.get(models.ChatSession, session_id)
        if chat is None or (chat.created_by_id != user.id and user.role != "admin"):
            await websocket.close(code=4404, reason="Chat session not found")
            return
        await websocket.send_json(
            {
                "type": "session",
                "session": {
                    "id": chat.id,
                    "title": chat.title,
                    "model": chat.model,
                    "created_at": chat.created_at.isoformat(),
                    "updated_at": chat.updated_at.isoformat(),
                },
                "messages": [
                    {
                        "id": m.id,
                        "role": m.role,
                        "content": m.content,
                        "steps": m.steps or [],
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in chat.messages
                ],
            }
        )

    loop = asyncio.get_running_loop()
    # One merged event stream: frames from the client ("client"/"closed") and
    # events from the agent thread ("agent"); avoids racing two queue reads.
    events: asyncio.Queue = asyncio.Queue()

    async def reader() -> None:
        while True:
            try:
                frame = await websocket.receive_json()
            except WebSocketDisconnect:
                events.put_nowait(("closed", None))
                return
            except RuntimeError:  # receive after disconnect
                events.put_nowait(("closed", None))
                return
            except ValueError:  # malformed JSON frame — ignore it
                continue
            events.put_nowait(("client", frame))

    reader_task = asyncio.create_task(reader())

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(events.put_nowait, ("agent", event))

    try:
        while True:
            kind, payload = await events.get()
            if kind == "closed":
                return
            if kind != "client" or not isinstance(payload, dict):
                continue
            if payload.get("type") != "user_message":
                continue
            content = str(payload.get("content", "")).strip()
            if not content:
                continue
            if len(content) > 8000:
                await websocket.send_json(
                    {"type": "error", "detail": "Message too long (8000 chars max)"}
                )
                continue

            cancel = threading.Event()
            turn = loop.run_in_executor(
                None, _turn_with_sentinel, session_id, user.id, content, emit, cancel
            )
            disconnected = False
            while True:
                kind, payload = await events.get()
                if kind == "agent":
                    if payload is None:  # sentinel: turn thread finished
                        break
                    if not disconnected:
                        try:
                            await websocket.send_json(payload)
                        except (WebSocketDisconnect, RuntimeError):
                            disconnected = True
                            cancel.set()
                elif kind == "client":
                    if isinstance(payload, dict) and payload.get("type") == "stop":
                        cancel.set()
                    elif isinstance(payload, dict) and payload.get("type") == "user_message":
                        await websocket.send_json(
                            {"type": "error", "detail": "Wait for the current answer to finish"}
                        )
                else:  # closed
                    disconnected = True
                    cancel.set()
            await turn
            if disconnected:
                return
    finally:
        reader_task.cancel()


def _turn_with_sentinel(
    session_id: int, user_id: int, content: str, emit, cancel: threading.Event
) -> None:
    try:
        run_chat_turn(session_id, user_id, content, emit, cancel)
    except Exception:  # noqa: BLE001 - run_chat_turn handles its own errors; belt & braces
        log.exception("chat turn crashed (session %s)", session_id)
        emit({"type": "error", "detail": "Internal error while answering"})
        emit({"type": "done"})
    finally:
        emit(None)  # sentinel for the WS pump
