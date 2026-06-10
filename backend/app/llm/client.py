"""Anthropic client wrapper + the shared SQL-tool agentic loop.

All LLM features degrade gracefully: callers must check llm_enabled() and fall
back (heuristics for check generation, 503 for explorer/RCA).
"""

import json
import logging
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

# Models with adaptive thinking support (Fable / Opus 4.6+ / Sonnet 4.6).
_ADAPTIVE_RE = re.compile(r"(fable|opus-4-[6-9]|sonnet-4-[6-9])")


def llm_enabled() -> bool:
    return get_settings().llm_enabled


@lru_cache
def _client():
    import anthropic

    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key)


def enabled_mcp_servers() -> list[dict[str, Any]]:
    """Admin-registered MCP servers (issue #36) to pass via the Claude MCP connector."""
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


def create_message(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    json_schema: dict[str, Any] | None = None,
    max_tokens: int | None = None,
    use_mcp: bool = False,
):
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "max_tokens": max_tokens or settings.llm_max_output_tokens,
        "system": system,
        "messages": messages,
    }
    if _ADAPTIVE_RE.search(settings.llm_model):
        kwargs["thinking"] = {"type": "adaptive"}
    if tools:
        kwargs["tools"] = tools
    if json_schema:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}

    if use_mcp:
        servers = enabled_mcp_servers()
        if servers:
            try:
                return _client().beta.messages.create(
                    **kwargs, mcp_servers=servers, betas=["mcp-client-2025-11-20"]
                )
            except Exception as exc:  # noqa: BLE001 - MCP connector is best-effort (experimental)
                log.warning("MCP connector call failed (%s); retrying without MCP servers", exc)
    return _client().messages.create(**kwargs)


def extract_text(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text")


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
    """Generic bounded agentic loop: call handler tools until the model calls
    `final_tool`. Returns the final tool's input dict, or None if the loop expired.
    The transcript list is appended to in place (text/sql/tool/result/final steps).
    """
    all_tools = [*tools, final_tool]
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    nudges = 0

    for _turn in range(max_turns):
        response = create_message(system=system, messages=messages, tools=all_tools, use_mcp=True)
        messages.append({"role": "assistant", "content": response.content})

        text = extract_text(response)
        if text.strip():
            transcript.append({"type": "text", "content": text.strip()})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            if response.stop_reason == "pause_turn":
                continue
            nudges += 1
            if nudges > 2:
                break
            messages.append(
                {
                    "role": "user",
                    "content": f"When you are done investigating, call the `{final_tool['name']}` tool "
                    "with your conclusions. Continue now.",
                }
            )
            continue

        results = []
        finished: dict[str, Any] | None = None
        for block in tool_uses:
            if block.name == final_tool["name"]:
                finished = dict(block.input)
                transcript.append({"type": "final", "content": finished})
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": "Report recorded."}
                )
            elif block.name in handlers:
                if block.name == "run_sql":
                    transcript.append(
                        {
                            "type": "sql",
                            "purpose": str(block.input.get("purpose", "")),
                            "sql": str(block.input.get("sql", "")),
                        }
                    )
                else:
                    transcript.append({"type": "tool", "name": block.name, "content": dict(block.input)})
                try:
                    output = handlers[block.name](dict(block.input))
                    is_error = False
                except Exception as exc:  # noqa: BLE001 - feed errors back so the agent adapts
                    output = f"{block.name} failed: {type(exc).__name__}: {exc}"
                    is_error = True
                transcript.append({"type": "result", "content": output[:4000], "error": is_error})
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output[:8000],
                        "is_error": is_error,
                    }
                )
            else:  # tool executed server-side (e.g. MCP connector) or unknown
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Unknown tool",
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": results})
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
