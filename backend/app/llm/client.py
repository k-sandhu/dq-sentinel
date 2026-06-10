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


def create_message(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    json_schema: dict[str, Any] | None = None,
    max_tokens: int | None = None,
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


def run_agent_loop(
    system: str,
    user_prompt: str,
    execute_sql: Callable[[str], str],
    final_tool: dict[str, Any],
    max_turns: int,
    transcript: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Generic bounded agentic loop: run_sql until the model calls `final_tool`.

    Returns the final tool's input dict, or None if the loop expired.
    The transcript list is appended to in place (text/sql/result/final steps).
    """
    tools = [RUN_SQL_TOOL, final_tool]
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    nudges = 0

    for _turn in range(max_turns):
        response = create_message(system=system, messages=messages, tools=tools)
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
            elif block.name == "run_sql":
                sql = str(block.input.get("sql", ""))
                purpose = str(block.input.get("purpose", ""))
                transcript.append({"type": "sql", "purpose": purpose, "sql": sql})
                try:
                    output = execute_sql(sql)
                    is_error = False
                except Exception as exc:  # noqa: BLE001 - feed errors back so the agent adapts
                    output = f"Query failed: {type(exc).__name__}: {exc}"
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
            else:  # unknown tool — shouldn't happen
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
