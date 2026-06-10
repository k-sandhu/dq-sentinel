"""LLM-authored investigation queries and ad-hoc dashboards (workbench features)."""

import logging
from typing import Any

from app.core.adhoc import normalize_panels
from app.core.suggest import validated
from app.llm import prompts
from app.llm.client import complete, parse_json_text

log = logging.getLogger(__name__)

SUGGESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "sql": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["title", "sql", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}

DASHBOARD_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "sql": {"type": "string"},
                    "viz": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["number", "bar", "line", "area", "pie", "table"],
                            },
                            "x": {"type": ["string", "null"]},
                            "y": {"type": ["string", "null"]},
                        },
                        "required": ["type", "x", "y"],
                        "additionalProperties": False,
                    },
                },
                "required": ["title", "description", "sql", "viz"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "panels"],
    "additionalProperties": False,
}


def suggest_queries_llm(context_text: str) -> list[dict[str, str]]:
    """Returns validated suggestions; raises if the model produced nothing usable."""
    response = complete(
        system=prompts.SUGGEST_SYSTEM,
        user_prompt=context_text,
        json_schema=SUGGESTIONS_SCHEMA,
        use_mcp=True,
    )
    data = parse_json_text(response.text)
    suggestions = validated(data.get("suggestions", []))
    if not suggestions:
        raise RuntimeError("LLM produced no guard-passing suggestions")
    return suggestions


def generate_dashboard_llm(context_text: str) -> dict[str, Any]:
    """Returns {title, panels(normalized)}; raises if no usable panels."""
    response = complete(
        system=prompts.DASHBOARD_SYSTEM,
        user_prompt=context_text,
        json_schema=DASHBOARD_SCHEMA,
        use_mcp=True,
    )
    data = parse_json_text(response.text)
    panels = normalize_panels(data.get("panels", []))
    if not panels:
        raise RuntimeError("LLM produced no guard-passing panels")
    return {"title": str(data.get("title") or "Investigation dashboard")[:300], "panels": panels}
