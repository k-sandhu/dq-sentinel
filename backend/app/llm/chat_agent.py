"""Conversational assistant: RCA, dataset/platform Q&A, and inline charts.

Each WebSocket turn runs run_chat_turn() in a worker thread; it streams events
(steps, status, persisted messages) through an emit() callback and persists the
user/assistant messages. The provider-native LLM history lives in a small
in-process cache keyed by session id and is rehydrated from persisted messages
after a restart (as plain text turns).
"""

import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from app.config import get_settings
from app.connectors.sa import Connector, connector_for
from app.core.adhoc import VIZ_TYPES, execute_panels
from app.core.profiler import summarize_profile_for_llm
from app.db import session_factory
from app.llm import client as llm_client
from app.llm import prompts
from app.llm.client import format_rows, redact_rows
from app.llm.providers import LlmResponse
from app.models import (
    ChatMessage,
    ChatSession,
    Check,
    CheckRun,
    Connection,
    Dataset,
    ExceptionRecord,
    Profile,
    utcnow,
)

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], None]

STEP_RESULT_MAX = 4000  # chars persisted/shown per tool result
TOOL_RESULT_MAX = 8000  # chars fed back to the model

# ------------------------------------------------------------------ tools
CHAT_RUN_SQL_TOOL = {
    "name": "run_sql",
    "description": (
        "Run a read-only SQL query (single SELECT or WITH statement) against a source "
        "database, identified by connection_id (see the platform state for which "
        "connection each dataset lives on). Results are row-capped; aggregate where possible."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "connection_id": {"type": "integer", "description": "Source connection id"},
            "sql": {"type": "string", "description": "One SELECT/WITH statement. No writes/DDL."},
            "purpose": {"type": "string", "description": "One line: what this query answers"},
        },
        "required": ["connection_id", "sql", "purpose"],
        "additionalProperties": False,
    },
}

CHAT_GET_TABLE_CODE_TOOL = {
    "name": "get_table_code",
    "description": (
        "Fetch how a table or view in a source database is defined (CREATE statement / view "
        "SQL where exposed, otherwise synthesized column definitions)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "connection_id": {"type": "integer", "description": "Source connection id"},
            "table": {"type": "string", "description": "Table or view name"},
        },
        "required": ["connection_id", "table"],
        "additionalProperties": False,
    },
}

DATASET_OVERVIEW_TOOL = {
    "name": "get_dataset_overview",
    "description": (
        "Everything DQ Sentinel knows about one registered dataset: latest profile summary, "
        "table knowledge from the data team, its checks with their last status, recent runs, "
        "and open exceptions with sample violating rows. Call this before investigating a dataset."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"dataset_id": {"type": "integer", "description": "Registered dataset id"}},
        "required": ["dataset_id"],
        "additionalProperties": False,
    },
}

RECENT_FAILURES_TOOL = {
    "name": "get_recent_failures",
    "description": (
        "Platform health right now: recent failed/erroring check runs across all datasets and "
        "open exception counts per dataset. Use for questions like 'what is broken?'."
    ),
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

RENDER_CHART_TOOL = {
    "name": "render_chart",
    "description": (
        "Run a read-only SQL query and render the result to the user as an inline chart. "
        "Use for trends, distributions, and segment comparisons. Keep results small "
        "(aggregate to <=100 rows; <=8 slices for pie). Alias output columns with short "
        "lowercase names and reference them as x/y."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "connection_id": {"type": "integer", "description": "Source connection id"},
            "sql": {"type": "string", "description": "One SELECT/WITH statement producing the data"},
            "title": {"type": "string", "description": "Short chart title shown to the user"},
            "chart_type": {
                "type": "string",
                "enum": ["number", "bar", "line", "area", "pie", "table"],
                "description": "Visualization type",
            },
            "x": {"type": ["string", "null"], "description": "Column for the x axis / labels"},
            "y": {"type": ["string", "null"], "description": "Column for the y axis / values"},
        },
        "required": ["connection_id", "sql", "title", "chart_type", "x", "y"],
        "additionalProperties": False,
    },
}

CHAT_TOOLS = [
    DATASET_OVERVIEW_TOOL,
    RECENT_FAILURES_TOOL,
    CHAT_RUN_SQL_TOOL,
    CHAT_GET_TABLE_CODE_TOOL,
    RENDER_CHART_TOOL,
]

# ------------------------------------------------- in-process LLM history
_HISTORY_MAX_SESSIONS = 32
_histories: OrderedDict[int, list[dict[str, Any]]] = OrderedDict()
_histories_lock = threading.Lock()


def drop_history(session_id: int) -> None:
    with _histories_lock:
        _histories.pop(session_id, None)


def _history_for(db, session_id: int) -> list[dict[str, Any]]:
    with _histories_lock:
        cached = _histories.get(session_id)
        if cached is not None:
            _histories.move_to_end(session_id)
            return cached
    history = _rehydrate(db, session_id)
    with _histories_lock:
        _histories[session_id] = history
        while len(_histories) > _HISTORY_MAX_SESSIONS:
            _histories.popitem(last=False)
    return history


def _rehydrate(db, session_id: int) -> list[dict[str, Any]]:
    """Rebuild a provider-agnostic history from persisted messages (text only).
    Consecutive same-role turns are merged so providers that require strict
    user/assistant alternation never see an invalid sequence."""
    history: list[dict[str, Any]] = []
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
        .all()
    )
    for m in messages:
        text = (m.content or "").strip() or "..."
        if m.role == "user":
            if history and history[-1]["role"] == "user":
                history[-1]["text"] += f"\n\n{text}"
            else:
                history.append({"role": "user", "text": text})
        else:
            if history and history[-1]["role"] == "assistant":
                prev = history[-1]["response"]
                history[-1]["response"] = LlmResponse(text=f"{prev.text}\n\n{text}")
            else:
                history.append({"role": "assistant", "response": LlmResponse(text=text)})
    return history


# ------------------------------------------------------------ turn context
def _connector(db, connection_id: int) -> Connector:
    conn = db.get(Connection, int(connection_id))
    if conn is None:
        raise ValueError(f"Connection {connection_id} not found — see the platform state for valid ids")
    return connector_for(conn)


def _pii_for_connection(db, connection_id: int) -> list[str]:
    """Union of knowledge.pii_columns across the connection's datasets: chat
    queries are connection-scoped, so redact any column name marked PII anywhere."""
    out: set[str] = set()
    for ds in db.query(Dataset).filter(Dataset.connection_id == int(connection_id)).all():
        if ds.knowledge and ds.knowledge.pii_columns:
            out.update(str(c) for c in ds.knowledge.pii_columns)
    return sorted(out)


def _system_prompt(db) -> str:
    datasets = db.query(Dataset).order_by(Dataset.id).limit(60).all()
    ds_rows = [
        {
            "id": d.id,
            "table": d.table_name if not d.schema_name else f"{d.schema_name}.{d.table_name}",
            "connection_id": d.connection_id,
            "engine": d.connection.kind,
            "rows": d.row_count if d.row_count is not None else "?",
        }
        for d in datasets
    ]
    open_exceptions = (
        db.query(ExceptionRecord).filter(ExceptionRecord.status == "open").count()
    )
    failures = []
    runs = (
        db.query(CheckRun)
        .filter(CheckRun.status.in_(["fail", "error"]))
        .order_by(CheckRun.id.desc())
        .limit(5)
        .all()
    )
    for r in runs:
        check = db.get(Check, r.check_id)
        ds = db.get(Dataset, r.dataset_id)
        failures.append(
            f"run #{r.id}: check '{check.name if check else '?'}' on {ds.table_name if ds else '?'} "
            f"-> {r.status}, {r.violation_count} violations, {r.started_at:%Y-%m-%d %H:%M} UTC"
        )
    return prompts.CHAT_SYSTEM + "\n" + prompts.chat_context_block(ds_rows, failures, open_exceptions)


def _dataset_overview(db, inp: dict[str, Any]) -> str:
    ds = db.get(Dataset, int(inp.get("dataset_id", 0)))
    if ds is None:
        raise ValueError(f"Dataset {inp.get('dataset_id')} not found")
    connector = connector_for(ds.connection)
    ref = connector.table_ref(ds.table_name, ds.schema_name)
    knowledge = ds.knowledge
    pii = list(knowledge.pii_columns or []) if knowledge else []

    parts = [
        f"Dataset {ds.id}: {ref} (connection_id={ds.connection_id}, dialect={connector.kind})",
        "",
        "## Table knowledge",
        prompts.knowledge_block(
            {
                "business_context": knowledge.business_context,
                "known_issues": knowledge.known_issues,
                "importance": knowledge.importance,
                "owner": knowledge.owner,
                "freshness_sla_hours": knowledge.freshness_sla_hours,
                "pii_columns": knowledge.pii_columns,
                "notes": knowledge.notes,
            }
            if knowledge
            else None
        ),
    ]

    profile = db.query(Profile).filter(Profile.dataset_id == ds.id).order_by(Profile.id.desc()).first()
    parts += [
        "",
        "## Latest profile",
        summarize_profile_for_llm(
            {
                "row_count": profile.row_count,
                "sampled_rows": profile.sampled_rows,
                "columns": profile.columns,
                "table_facts": profile.table_facts,
            },
            pii,
        )
        if profile
        else "(not profiled yet)",
    ]

    checks = db.query(Check).filter(Check.dataset_id == ds.id).order_by(Check.id).limit(40).all()
    parts += ["", f"## Checks ({len(checks)})"]
    parts += [
        f"- #{c.id} {c.name} [{c.check_type}{f' on {c.column_name}' if c.column_name else ''}] "
        f"status={c.status}, last_run={c.last_status or 'never'}"
        for c in checks
    ] or ["(no checks yet)"]

    runs = (
        db.query(CheckRun)
        .filter(CheckRun.dataset_id == ds.id)
        .order_by(CheckRun.id.desc())
        .limit(10)
        .all()
    )
    parts += ["", "## Recent runs"]
    parts += [
        f"- run #{r.id} (check #{r.check_id}) {r.started_at:%Y-%m-%d %H:%M} UTC -> "
        f"{r.status}, {r.violation_count} violations"
        for r in runs
    ] or ["(no runs yet)"]

    open_q = db.query(ExceptionRecord).filter(
        ExceptionRecord.dataset_id == ds.id, ExceptionRecord.status == "open"
    )
    parts += ["", f"## Open exceptions: {open_q.count()}"]
    pii_lower = {c.lower() for c in pii}
    for e in open_q.order_by(ExceptionRecord.id.desc()).limit(3).all():
        row = {
            k: ("[REDACTED]" if k.lower() in pii_lower else v) for k, v in (e.row_data or {}).items()
        }
        parts.append(f"- #{e.id} ({e.reason or 'no reason'}): {json.dumps(row, default=str)[:300]}")
    return "\n".join(parts)


def _recent_failures(db, _inp: dict[str, Any]) -> str:
    runs = (
        db.query(CheckRun)
        .filter(CheckRun.status.in_(["fail", "error"]))
        .order_by(CheckRun.id.desc())
        .limit(15)
        .all()
    )
    parts = ["## Recent failed/erroring runs"]
    for r in runs:
        check = db.get(Check, r.check_id)
        ds = db.get(Dataset, r.dataset_id)
        line = (
            f"- run #{r.id}: check '{check.name if check else '?'}' on "
            f"{ds.table_name if ds else '?'} (dataset_id={r.dataset_id}) -> {r.status}, "
            f"{r.violation_count} violations, {r.started_at:%Y-%m-%d %H:%M} UTC"
        )
        if r.error_message:
            line += f", error: {r.error_message[:160]}"
        parts.append(line)
    if not runs:
        parts.append("(none — all recent runs passed)")

    parts.append("")
    parts.append("## Open exceptions by dataset")
    rows = (
        db.query(ExceptionRecord.dataset_id)
        .filter(ExceptionRecord.status == "open")
        .all()
    )
    counts: dict[int, int] = {}
    for (dataset_id,) in rows:
        counts[dataset_id] = counts.get(dataset_id, 0) + 1
    for dataset_id, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        ds = db.get(Dataset, dataset_id)
        parts.append(f"- {ds.table_name if ds else '?'} (dataset_id={dataset_id}): {n}")
    if not counts:
        parts.append("(none open)")
    return "\n".join(parts)


# ------------------------------------------------------------- the turn
_turn_locks: dict[int, threading.Lock] = {}
_turn_locks_guard = threading.Lock()


def _turn_lock(session_id: int) -> threading.Lock:
    with _turn_locks_guard:
        return _turn_locks.setdefault(session_id, threading.Lock())


def run_chat_turn(
    session_id: int,
    user_id: int,
    content: str,
    emit: Emit,
    cancel: threading.Event,
) -> None:
    """Synchronous turn executor (run in a worker thread by the WS endpoint).
    Emits WS events via emit(); always persists the user + assistant messages."""
    lock = _turn_lock(session_id)
    if not lock.acquire(blocking=False):
        emit({"type": "error", "detail": "Another answer is already in progress for this session"})
        emit({"type": "done"})
        return
    try:
        _run_turn_locked(session_id, user_id, content, emit, cancel)
    finally:
        lock.release()


def _run_turn_locked(
    session_id: int,
    user_id: int,
    content: str,
    emit: Emit,
    cancel: threading.Event,
) -> None:
    settings = get_settings()
    factory = session_factory()
    with factory() as db:
        session = db.get(ChatSession, session_id)
        if session is None:
            emit({"type": "error", "detail": "Chat session no longer exists"})
            return

        user_msg = ChatMessage(session_id=session_id, role="user", content=content, steps=[])
        if not session.title:
            session.title = content[:80] + ("…" if len(content) > 80 else "")
        session.updated_at = utcnow()
        db.add(user_msg)
        db.commit()
        db.refresh(user_msg)
        emit({"type": "message_saved", "message": _message_payload(user_msg)})

        steps: list[dict[str, Any]] = []
        final_text = ""
        try:
            final_text = _run_loop(db, session_id, content, steps, emit, cancel, settings)
        except Exception as exc:  # noqa: BLE001 - persist + surface, never crash the socket
            log.exception("Chat turn failed (session %s)", session_id)
            # The in-memory history may end mid-tool-call; rebuild it from
            # persisted messages next turn instead of replaying a broken tail.
            drop_history(session_id)
            detail = f"{type(exc).__name__}: {exc}"
            steps.append({"type": "error", "content": detail})
            final_text = f"Something went wrong while answering: {detail}"
            emit({"type": "error", "detail": detail})

        assistant = ChatMessage(
            session_id=session_id, role="assistant", content=final_text, steps=steps
        )
        resolved = settings.resolved_llm()
        session.model = (resolved or {}).get("model") or session.model
        session.updated_at = utcnow()
        db.add(assistant)
        db.commit()
        db.refresh(assistant)
        emit({"type": "assistant_message", "message": _message_payload(assistant)})
        emit({"type": "done"})


def _message_payload(m: ChatMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "steps": m.steps or [],
        "created_at": m.created_at.isoformat(),
    }


def _run_loop(
    db,
    session_id: int,
    content: str,
    steps: list[dict[str, Any]],
    emit: Emit,
    cancel: threading.Event,
    settings,
) -> str:
    provider = llm_client.get_provider()
    if provider is None:
        raise RuntimeError(
            "No LLM provider configured. Set DQ_LLM_API_KEY + DQ_LLM_MODEL "
            "(OpenRouter / any OpenAI-compatible endpoint) or ANTHROPIC_API_KEY."
        )

    def step(payload: dict[str, Any]) -> None:
        steps.append(payload)
        emit({"type": "step", "step": payload})

    def run_sql(inp: dict[str, Any]) -> str:
        connector = _connector(db, inp.get("connection_id", 0))
        pii = _pii_for_connection(db, inp.get("connection_id", 0))
        res = connector.run_select(str(inp.get("sql", "")), limit=settings.agent_query_row_limit)
        return format_rows(res.columns, redact_rows(res.columns, res.rows, pii))

    def get_code(inp: dict[str, Any]) -> str:
        connector = _connector(db, inp.get("connection_id", 0))
        ddl, source = connector.get_ddl(str(inp.get("table", "")))
        return f"-- definition source: {source}\n{ddl}"

    def render_chart(inp: dict[str, Any]) -> str:
        connector = _connector(db, inp.get("connection_id", 0))
        viz_type = inp.get("chart_type") if inp.get("chart_type") in VIZ_TYPES else "table"
        panel = {
            "title": str(inp.get("title") or "Chart")[:300],
            "description": "",
            "sql": str(inp.get("sql", "")),
            "viz": {"type": viz_type, "x": inp.get("x"), "y": inp.get("y")},
        }
        result = execute_panels(connector, [panel])[0]
        if result["error"]:
            raise ValueError(f"Chart query failed: {result['error']}")
        pii = _pii_for_connection(db, inp.get("connection_id", 0))
        result["rows"] = redact_rows(result["columns"], result["rows"], pii)
        step(
            {
                "type": "chart",
                "title": result["title"],
                "sql": result["sql"],
                "viz": result["viz"],
                "columns": result["columns"],
                "rows": result["rows"],
                "elapsed_ms": result["elapsed_ms"],
            }
        )
        preview = format_rows(result["columns"], result["rows"][:15])
        return (
            f"Chart '{result['title']}' ({viz_type}) is now displayed to the user. "
            f"Data preview:\n{preview}"
        )

    handlers = {
        "run_sql": run_sql,
        "get_table_code": get_code,
        "get_dataset_overview": lambda inp: _dataset_overview(db, inp),
        "get_recent_failures": lambda inp: _recent_failures(db, inp),
        "render_chart": render_chart,
    }

    system = _system_prompt(db)
    history = _history_for(db, session_id)
    history.append({"role": "user", "text": content})

    final_text = ""
    nudges = 0
    for _turn in range(settings.llm_max_chat_turns):
        if cancel.is_set():
            return final_text or "(stopped)"
        emit({"type": "status", "state": "thinking"})
        response = provider.complete(system, history, tools=CHAT_TOOLS, use_mcp=True)
        history.append({"role": "assistant", "response": response})

        if response.text.strip():
            final_text = response.text.strip()
            step({"type": "text", "content": final_text})

        if not response.tool_calls:
            if response.stop_reason == "pause":
                continue
            if final_text:
                return final_text
            nudges += 1
            if nudges > 2:
                break
            history.append(
                {"role": "user", "text": "Please answer the question now, in plain markdown."}
            )
            continue

        results = []
        for call in response.tool_calls:
            if cancel.is_set():
                results.append({"id": call.id, "content": "Cancelled by the user.", "is_error": True})
                continue
            emit(
                {
                    "type": "status",
                    "state": "tool",
                    "tool": call.name,
                    "detail": str(call.input.get("purpose") or call.input.get("title") or ""),
                }
            )
            if call.name == "run_sql":
                step(
                    {
                        "type": "sql",
                        "purpose": str(call.input.get("purpose", "")),
                        "sql": str(call.input.get("sql", "")),
                    }
                )
            elif call.name != "render_chart":  # render_chart emits its own chart step
                step({"type": "tool", "name": call.name, "content": dict(call.input)})
            if call.name in handlers:
                try:
                    output = handlers[call.name](dict(call.input))
                    is_error = False
                except Exception as exc:  # noqa: BLE001 - feed errors back so the model adapts
                    output = f"{call.name} failed: {type(exc).__name__}: {exc}"
                    is_error = True
                if call.name != "render_chart" or is_error:
                    step({"type": "result", "content": output[:STEP_RESULT_MAX], "error": is_error})
                results.append({"id": call.id, "content": output[:TOOL_RESULT_MAX], "is_error": is_error})
            else:
                results.append({"id": call.id, "content": "Unknown tool", "is_error": True})
        history.append({"role": "tool_results", "results": results})

    if cancel.is_set():
        return final_text or "(stopped)"
    return final_text or (
        "I could not finish answering within the investigation budget. "
        "Try a narrower question, or ask me to continue."
    )
