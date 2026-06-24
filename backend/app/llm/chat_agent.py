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
from app.core import check_authoring
from app.core import sla as sla_core
from app.core.adhoc import VIZ_TYPES, execute_panels
from app.core.check_types import CHECK_TYPES
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
    User,
    utcnow,
)
from app.security import ROLE_RANK, connection_role, visible_connection_ids, visible_dataset_ids

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

# ------------------------------------------------------ authoring tools (#186)
LIST_CHECK_TYPES_TOOL = {
    "name": "list_check_types",
    "description": (
        "List every data-quality check type you can author, with each type's parameters "
        "(name, whether required, type, default). Call this before proposing or creating a check "
        "so you use a valid check_type and correct parameter names."
    ),
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

CREATE_CHECK_TOOL = {
    "name": "create_check",
    "description": (
        "Create a data-quality check on a registered dataset. ONLY call this AFTER you have shown "
        "the user the exact configuration and they have explicitly approved it in the conversation — "
        "never create a check the user has not confirmed. Use list_check_types for valid check_type "
        "values and their params, and set thresholds from the actual data (profile / queries), not "
        "guesses. The check is created live and its configuration is saved as version 1 (rollback-able)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "dataset_id": {"type": "integer", "description": "Registered dataset id the check runs on"},
            "check_type": {"type": "string", "description": "A key from list_check_types (e.g. not_null, range, freshness, custom_sql)"},
            "column_name": {"type": ["string", "null"], "description": "Column to check, or null for table-level checks"},
            "params": {"type": "object", "description": "Check-type-specific parameters (see list_check_types)"},
            "severity": {"type": "string", "enum": ["info", "warn", "error"], "description": "info=monitor, warn=investigate, error=page/block"},
            "name": {"type": ["string", "null"], "description": "Optional display name; a sensible default is generated if null"},
            "rationale": {"type": ["string", "null"], "description": "One line: why this check exists / what it protects"},
            "schedule_kind": {"type": ["string", "null"], "description": "'interval' (minutes) or 'cron'; null defaults to interval"},
            "schedule_expr": {"type": ["string", "null"], "description": "Minutes for interval ('1440'=daily, '360'=6-hourly) or a cron string; null defaults to 1440"},
        },
        "required": ["dataset_id", "check_type", "column_name", "params", "severity", "name", "rationale", "schedule_kind", "schedule_expr"],
        "additionalProperties": False,
    },
}

UPDATE_CHECK_TOOL = {
    "name": "update_check",
    "description": (
        "Edit an existing check's configuration or lifecycle (pause/resume). ONLY call this after the "
        "user has approved the change. Pass only the fields you want to change; leave the rest null. "
        "Definition changes are versioned and can be rolled back. To change the threshold of a check, "
        "pass its new params object."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "check_id": {"type": "integer", "description": "Id of the check to edit"},
            "name": {"type": ["string", "null"]},
            "column_name": {"type": ["string", "null"], "description": "New column (rarely changed); null = leave unchanged"},
            "params": {"type": ["object", "null"], "description": "Replacement parameters object; null = leave unchanged"},
            "severity": {"type": ["string", "null"], "enum": ["info", "warn", "error", None]},
            "rationale": {"type": ["string", "null"]},
            "schedule_kind": {"type": ["string", "null"]},
            "schedule_expr": {"type": ["string", "null"]},
            "status": {"type": ["string", "null"], "enum": ["active", "disabled", "proposed", "archived", None], "description": "Lifecycle: active=running, disabled=paused"},
        },
        "required": ["check_id", "name", "column_name", "params", "severity", "rationale", "schedule_kind", "schedule_expr", "status"],
        "additionalProperties": False,
    },
}

CREATE_SLA_TOOL = {
    "name": "create_sla",
    "description": (
        "Create a reliability SLA that tracks check-run success over a rolling window and alerts when "
        "attainment falls below the objective. ONLY call this after the user has approved it. scope='dataset' "
        "rolls up the dataset's checks (target_type freshness | volume | check_success); scope='check' tracks "
        "one check (pass scope_id=check id)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": ["string", "null"], "description": "Optional name; a default is generated if null"},
            "scope": {"type": "string", "enum": ["dataset", "check"], "description": "What the SLA covers"},
            "scope_id": {"type": "integer", "description": "dataset_id when scope=dataset, else check id"},
            "target_type": {"type": "string", "enum": ["freshness", "volume", "check_success"], "description": "Which checks count toward attainment"},
            "objective": {"type": "number", "description": "Target attainment fraction in (0, 1], e.g. 0.99"},
            "window": {"type": "string", "enum": ["rolling_7d", "rolling_30d"], "description": "Rolling evaluation window"},
            "enabled": {"type": "boolean", "description": "Whether the SLA is active"},
        },
        "required": ["name", "scope", "scope_id", "target_type", "objective", "window", "enabled"],
        "additionalProperties": False,
    },
}

CHAT_TOOLS = [
    DATASET_OVERVIEW_TOOL,
    RECENT_FAILURES_TOOL,
    CHAT_RUN_SQL_TOOL,
    CHAT_GET_TABLE_CODE_TOOL,
    RENDER_CHART_TOOL,
    LIST_CHECK_TYPES_TOOL,
    CREATE_CHECK_TOOL,
    UPDATE_CHECK_TOOL,
    CREATE_SLA_TOOL,
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


def _system_prompt(db, user: User) -> str:
    # Scope the platform-state the model sees to the user's grants (#159): a granted
    # user's assistant must not be told about datasets/runs on other connections.
    vis = visible_connection_ids(db, user)  # None -> unrestricted (admin / zero-grant)
    visible_ds = visible_dataset_ids(db, user)
    ds_q = db.query(Dataset)
    if vis is not None:
        ds_q = ds_q.filter(Dataset.connection_id.in_(vis))
    datasets = ds_q.order_by(Dataset.id).limit(60).all()
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
    open_exc_q = db.query(ExceptionRecord).filter(ExceptionRecord.status == "open")
    if visible_ds is not None:
        open_exc_q = open_exc_q.filter(ExceptionRecord.dataset_id.in_(visible_ds))
    open_exceptions = open_exc_q.count()
    failures = []
    runs_q = db.query(CheckRun).filter(CheckRun.status.in_(["fail", "error"]))
    if visible_ds is not None:
        runs_q = runs_q.filter(CheckRun.dataset_id.in_(visible_ds))
    runs = runs_q.order_by(CheckRun.id.desc()).limit(5).all()
    for r in runs:
        check = db.get(Check, r.check_id)
        ds = db.get(Dataset, r.dataset_id)
        failures.append(
            f"run #{r.id}: check '{check.name if check else '?'}' on {ds.table_name if ds else '?'} "
            f"-> {r.status}, {r.violation_count} violations, {r.started_at:%Y-%m-%d %H:%M} UTC"
        )
    return prompts.CHAT_SYSTEM + "\n" + prompts.chat_context_block(ds_rows, failures, open_exceptions)


def _dataset_overview(db, inp: dict[str, Any], user: User) -> str:
    ds = db.get(Dataset, int(inp.get("dataset_id", 0)))
    if ds is None:
        raise ValueError(f"Dataset {inp.get('dataset_id')} not found")
    if connection_role(db, user, ds.connection_id) is None:  # grant scope (#159)
        raise ValueError(f"Access denied: dataset {ds.id} is on a connection you can't access")
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
                "domain": knowledge.domain,
                "team": knowledge.team,
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


def _recent_failures(db, _inp: dict[str, Any], user: User) -> str:
    visible_ds = visible_dataset_ids(db, user)  # None -> unrestricted (#159)
    runs_q = db.query(CheckRun).filter(CheckRun.status.in_(["fail", "error"]))
    if visible_ds is not None:
        runs_q = runs_q.filter(CheckRun.dataset_id.in_(visible_ds))
    runs = runs_q.order_by(CheckRun.id.desc()).limit(15).all()
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
    open_q = db.query(ExceptionRecord.dataset_id).filter(ExceptionRecord.status == "open")
    if visible_ds is not None:
        open_q = open_q.filter(ExceptionRecord.dataset_id.in_(visible_ds))
    rows = open_q.all()
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
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            emit({"type": "error", "detail": "Session user no longer exists or is inactive"})
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
            final_text = _run_loop(db, session_id, content, steps, emit, cancel, settings, user)
        except Exception as exc:  # noqa: BLE001 - persist + surface, never crash the socket
            log.exception("Chat turn failed (session %s)", session_id)
            # The in-memory history may end mid-tool-call; rebuild it from
            # persisted messages next turn instead of replaying a broken tail.
            drop_history(session_id)
            # Full detail goes to the log above; the UI gets a safe summary.
            detail = llm_client.safe_user_error(exc)
            steps.append({"type": "error", "content": detail})
            final_text = detail
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
    user: User,
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

    def conn_for(connection_id, min_role: str = "viewer") -> Connector:
        # The model can name any connection_id; authorize against the session user's
        # effective grant role before connecting (#159). Tools that EXECUTE source SQL
        # (run_sql, render_chart) require editor-on-connection — the same bar as
        # POST /query/run — so a viewer-grant can't run arbitrary SQL via the
        # assistant; DDL/overview stay visibility-only. Raising surfaces a tool error
        # to the model (it can recover), not an HTTP 404.
        role = connection_role(db, user, int(connection_id or 0))
        if role is None:
            raise ValueError(f"Access denied: you do not have access to connection {connection_id}.")
        if ROLE_RANK.get(role, -1) < ROLE_RANK[min_role]:
            raise ValueError(
                f"Access denied: this action on connection {connection_id} requires editor "
                f"access; your effective role there is {role}."
            )
        return _connector(db, connection_id)

    def run_sql(inp: dict[str, Any]) -> str:
        connector = conn_for(inp.get("connection_id", 0), "editor")  # executes source SQL
        pii = _pii_for_connection(db, inp.get("connection_id", 0))
        res = connector.run_select(str(inp.get("sql", "")), limit=settings.agent_query_row_limit)
        return format_rows(res.columns, redact_rows(res.columns, res.rows, pii))

    def get_code(inp: dict[str, Any]) -> str:
        connector = conn_for(inp.get("connection_id", 0))
        ddl, source = connector.get_ddl(str(inp.get("table", "")))
        return f"-- definition source: {source}\n{ddl}"

    def render_chart(inp: dict[str, Any]) -> str:
        connector = conn_for(inp.get("connection_id", 0), "editor")  # executes source SQL
        viz_type = inp.get("chart_type") if inp.get("chart_type") in VIZ_TYPES else "table"
        panel = {
            "title": str(inp.get("title") or "Chart")[:300],
            "description": "",
            "sql": str(inp.get("sql", "")),
            "viz": {"type": viz_type, "x": inp.get("x"), "y": inp.get("y")},
        }
        # Same row cap as run_sql (200), not the 500-row dashboard cap (#159 / LLM-3).
        result = execute_panels(connector, [panel], limit=settings.agent_query_row_limit)[0]
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

    def require_dataset_editor(dataset_id: Any) -> Dataset:
        """Authorize an authoring action against the dataset's connection (#186):
        the WS gate is global-editor, but writing checks/SLAs requires editor on
        the specific connection — the same bar as the REST endpoints."""
        ds = db.get(Dataset, int(dataset_id or 0))
        if ds is None:
            raise ValueError(f"Dataset {dataset_id} not found — see the platform state for valid ids.")
        role = connection_role(db, user, ds.connection_id)
        if role is None or ROLE_RANK.get(role, -1) < ROLE_RANK["editor"]:
            raise ValueError(
                f"Access denied: authoring checks/SLAs on dataset {ds.id} requires editor access "
                f"to its connection."
            )
        return ds

    def list_check_types(_inp: dict[str, Any]) -> str:
        lines = []
        for ct in CHECK_TYPES.values():
            ps = ", ".join(
                f"{p['name']}{'*' if p['required'] else ''}:{p['type']}"
                + (f"={p['default']}" if p.get("default") not in (None, "", []) else "")
                for p in ct.params
            ) or "none"
            col = "needs a column" if ct.needs_column else "table-level"
            lines.append(f"- {ct.key}: {ct.description}. {col}. params: {ps}")
        return (
            "Check types you can author (use these keys + param names with create_check; "
            "* marks a required param):\n" + "\n".join(lines)
        )

    def create_check_tool(inp: dict[str, Any]) -> str:
        ds = require_dataset_editor(inp.get("dataset_id"))
        try:
            check = check_authoring.create_check(
                db, user, ds,
                name=inp.get("name") or "",
                check_type=str(inp.get("check_type") or ""),
                column_name=inp.get("column_name") or None,
                params=inp.get("params") or {},
                severity=str(inp.get("severity") or "warn"),
                rationale=inp.get("rationale") or "",
                schedule_kind=inp.get("schedule_kind") or "interval",
                schedule_expr=inp.get("schedule_expr") or "1440",
                status="active",
                change_note="created via assistant",
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(check)
        return (
            f"Created check #{check.id} '{check.name}' "
            f"[{check.check_type}{f' on {check.column_name}' if check.column_name else ''}], "
            f"severity={check.severity}, status={check.status}, "
            f"schedule={check.schedule_kind}:{check.schedule_expr}. It is live and saved as version 1 "
            f"(the user can roll it back from the check's history)."
        )

    def update_check_tool(inp: dict[str, Any]) -> str:
        check = db.get(Check, int(inp.get("check_id") or 0))
        if check is None:
            raise ValueError(f"Check {inp.get('check_id')} not found.")
        require_dataset_editor(check.dataset_id)
        fields = ("name", "column_name", "params", "severity", "rationale", "schedule_kind", "schedule_expr", "status")
        changes: dict[str, Any] = {k: inp[k] for k in fields if inp.get(k) is not None}
        if not changes:
            return "No changes were specified — nothing to update."
        changes["change_note"] = "edited via assistant"
        try:
            check_authoring.apply_update(db, user, check, changes)
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(check)
        return (
            f"Updated check #{check.id} '{check.name}' (changed: "
            f"{', '.join(k for k in changes if k != 'change_note')}). Definition changes are versioned "
            f"and can be rolled back."
        )

    def create_sla_tool(inp: dict[str, Any]) -> str:
        scope = str(inp.get("scope") or "dataset")
        scope_id = int(inp.get("scope_id") or 0)
        target_type = str(inp.get("target_type") or "check_success")
        window = str(inp.get("window") or "rolling_30d")
        raw_obj = inp.get("objective")  # don't use `or`: an explicit 0 must hit the guard below
        objective = float(raw_obj) if raw_obj is not None else 0.99
        if scope not in ("dataset", "check"):
            raise ValueError("scope must be 'dataset' or 'check'.")
        if target_type not in ("freshness", "volume", "check_success"):
            raise ValueError("target_type must be 'freshness', 'volume', or 'check_success'.")
        if window not in ("rolling_7d", "rolling_30d"):
            raise ValueError("window must be 'rolling_7d' or 'rolling_30d'.")
        if not 0 < objective <= 1:
            raise ValueError("objective must be a fraction in (0, 1], e.g. 0.99.")
        if scope == "check":
            chk = db.get(Check, scope_id)
            if chk is None:
                raise ValueError(f"Check {scope_id} not found.")
            require_dataset_editor(chk.dataset_id)
        else:
            require_dataset_editor(scope_id)
        try:
            sla = sla_core.create_sla_definition(
                db, user,
                name=inp.get("name") or "",
                scope=scope, scope_id=scope_id,
                target_type=target_type, objective=objective, window=window,
                enabled=bool(inp.get("enabled", True)),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(sla)
        latest = sla.evaluations[-1] if sla.evaluations else None
        att = f"{latest.attainment * 100:.1f}%" if latest else "n/a"
        return (
            f"Created SLA #{sla.id} '{sla.name}' — {sla.target_type} objective "
            f"{sla.objective * 100:.1f}% over {sla.window} (current attainment {att}). "
            f"It will alert when attainment falls below the objective."
        )

    handlers = {
        "run_sql": run_sql,
        "get_table_code": get_code,
        "get_dataset_overview": lambda inp: _dataset_overview(db, inp, user),
        "get_recent_failures": lambda inp: _recent_failures(db, inp, user),
        "render_chart": render_chart,
        "list_check_types": list_check_types,
        "create_check": create_check_tool,
        "update_check": update_check_tool,
        "create_sla": create_sla_tool,
    }

    system = _system_prompt(db, user)
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
