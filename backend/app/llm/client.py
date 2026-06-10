"""LLM entrypoints used across the app, built on the provider-agnostic layer
(app/llm/providers.py). All features degrade gracefully: callers must check
llm_enabled() and fall back (heuristics for generation, 503 for agents).
"""

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from app.config import get_settings
from app.llm.providers import LlmResponse, get_provider

log = logging.getLogger(__name__)


def llm_enabled() -> bool:
    return get_settings().llm_enabled


def provider_info() -> dict[str, Any]:
    resolved = get_settings().resolved_llm()
    if resolved is None:
        return {"llm_provider": None, "llm_model": None}
    return {"llm_provider": resolved["provider"], "llm_model": resolved["model"]}


def enabled_mcp_servers() -> list[dict[str, Any]]:
    """Admin-registered MCP servers (issue #36), attached on the Anthropic path."""
    from app.db import session_factory
    from app.models import McpServer

    try:
        with session_factory()() as db:
            servers = db.query(McpServer).filter(McpServer.enabled.is_(True)).all()
    except Exception:  # noqa: BLE001 - never let metadata-DB hiccups break LLM calls
        return []
    out = []
    for s in servers:
        entry: dict[str, Any] = {"type": "url", "name": s.name, "url": s.url}
        if s.auth_token:
            entry["authorization_token"] = s.auth_token
        out.append(entry)
    return out


def complete(
    system: str,
    user_prompt: str,
    tools: list[dict[str, Any]] | None = None,
    json_schema: dict[str, Any] | None = None,
    max_tokens: int | None = None,
    use_mcp: bool = False,
) -> LlmResponse:
    """One-shot completion on whichever provider is configured."""
    provider = get_provider()
    if provider is None:
        raise RuntimeError("No LLM provider configured")
    return provider.complete(
        system,
        [{"role": "user", "text": user_prompt}],
        tools=tools,
        json_schema=json_schema,
        max_tokens=max_tokens,
        use_mcp=use_mcp,
    )


def format_rows(columns: list[str], rows: list[list[Any]], max_cell: int = 120) -> str:
    """Compact pipe-table for tool results."""
    if not rows:
        return "(0 rows)"

    def cell(v: Any) -> str:
        s = "NULL" if v is None else str(v)
        return s[:max_cell] + "…" if len(s) > max_cell else s

    lines = [" | ".join(columns)]
    lines += [" | ".join(cell(v) for v in r) for r in rows]
    lines.append(f"({len(rows)} rows)")
    return "\n".join(lines)


def redact_rows(columns: list[str], rows: list[list[Any]], pii_columns: list[str]) -> list[list[Any]]:
    if not pii_columns:
        return rows
    pii = {c.lower() for c in pii_columns}
    idx = [i for i, c in enumerate(columns) if c.lower() in pii]
    if not idx:
        return rows
    out = []
    for r in rows:
        r = list(r)
        for i in idx:
            if r[i] is not None:
                r[i] = "[REDACTED]"
        out.append(r)
    return out


RUN_SQL_TOOL = {
    "name": "run_sql",
    "description": (
        "Run a read-only SQL query (single SELECT or WITH statement) against the dataset's "
        "source database. Results are capped; aggregate where possible. Call this when you "
        "need facts you don't already have — distributions, counts, joins, examples."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "One SELECT/WITH statement. No writes/DDL."},
            "purpose": {"type": "string", "description": "One line: what this query checks"},
        },
        "required": ["sql", "purpose"],
        "additionalProperties": False,
    },
}

GET_TABLE_CODE_TOOL = {
    "name": "get_table_code",
    "description": (
        "Fetch how a table or view in this source database is defined: its CREATE statement / "
        "view SQL where the database exposes it, otherwise a synthesized column definition. "
        "Use it when the data's structure or derivation matters to your investigation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"table": {"type": "string", "description": "Table or view name"}},
        "required": ["table"],
        "additionalProperties": False,
    },
}


def run_agent_loop(
    system: str,
    user_prompt: str,
    handlers: dict[str, Callable[[dict[str, Any]], str]],
    tools: list[dict[str, Any]],
    final_tool: dict[str, Any],
    max_turns: int,
    transcript: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Generic bounded agentic loop on the provider abstraction: call handler
    tools until the model calls `final_tool`. Returns the final tool's input
    dict, or None if the loop expired. The transcript list is appended to in
    place (text/sql/tool/result/final steps).
    """
    provider = get_provider()
    if provider is None:
        raise RuntimeError("No LLM provider configured")

    all_tools = [*tools, final_tool]
    history: list[dict[str, Any]] = [{"role": "user", "text": user_prompt}]
    nudges = 0

    for _turn in range(max_turns):
        response = provider.complete(system, history, tools=all_tools, use_mcp=True)
        history.append({"role": "assistant", "response": response})

        if response.text.strip():
            transcript.append({"type": "text", "content": response.text.strip()})

        if not response.tool_calls:
            if response.stop_reason == "pause":
                continue
            nudges += 1
            if nudges > 2:
                break
            history.append(
                {
                    "role": "user",
                    "text": f"When you are done investigating, call the `{final_tool['name']}` tool "
                    "with your conclusions. Continue now.",
                }
            )
            continue

        results = []
        finished: dict[str, Any] | None = None
        for call in response.tool_calls:
            if call.name == final_tool["name"]:
                finished = dict(call.input)
                transcript.append({"type": "final", "content": finished})
                results.append({"id": call.id, "content": "Report recorded.", "is_error": False})
            elif call.name in handlers:
                if call.name == "run_sql":
                    transcript.append(
                        {
                            "type": "sql",
                            "purpose": str(call.input.get("purpose", "")),
                            "sql": str(call.input.get("sql", "")),
                        }
                    )
                else:
                    transcript.append({"type": "tool", "name": call.name, "content": dict(call.input)})
                try:
                    output = handlers[call.name](dict(call.input))
                    is_error = False
                except Exception as exc:  # noqa: BLE001 - feed errors back so the agent adapts
                    output = f"{call.name} failed: {type(exc).__name__}: {exc}"
                    is_error = True
                transcript.append({"type": "result", "content": output[:4000], "error": is_error})
                results.append({"id": call.id, "content": output[:8000], "is_error": is_error})
            else:
                results.append({"id": call.id, "content": "Unknown tool", "is_error": True})
        history.append({"role": "tool_results", "results": results})
        if finished is not None:
            return finished

    transcript.append({"type": "text", "content": "(agent reached its turn limit before finishing)"})
    return None


def parse_json_text(text: str) -> Any:
    """Parse model JSON output, tolerating markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)
