"""LLM check generation: profile + knowledge (+ exploration) -> validated check proposals."""

import logging
from typing import Any

from app.core.check_types import CHECK_TYPES, validate_check
from app.llm import prompts
from app.llm.client import complete, parse_json_text

log = logging.getLogger(__name__)

_PARAM_PROPS = {
    "values": {"type": "array", "items": {"type": ["string", "number", "boolean"]}},
    "case_sensitive": {"type": "boolean"},
    "min": {"type": ["number", "string"]},
    "max": {"type": ["number", "string"]},
    "min_len": {"type": "number"},
    "max_len": {"type": "number"},
    "pattern": {"type": "string"},
    "max_age_hours": {"type": "number"},
    "min_rows": {"type": "number"},
    "sigma": {"type": "number"},
    "lookback_runs": {"type": "number"},
    "contamination": {"type": "number"},
    "columns": {"type": "array", "items": {"type": "string"}},
    "sql": {"type": "string"},
    "max_rows": {"type": "number"},
    "tolerance": {"type": "number"},
}

CHECKS_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "check_type": {"type": "string", "enum": sorted(CHECK_TYPES.keys())},
                    "column_name": {"type": ["string", "null"]},
                    "params": {
                        "type": "object",
                        "properties": _PARAM_PROPS,
                        "required": [],
                        "additionalProperties": False,
                    },
                    "severity": {"type": "string", "enum": ["info", "warn", "error"]},
                    "rationale": {"type": "string"},
                    "schedule_minutes": {"type": "number"},
                },
                "required": ["name", "check_type", "column_name", "params", "severity", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["checks"],
    "additionalProperties": False,
}


def generate_checks_llm(
    table_name: str,
    profile_summary: str,
    knowledge: dict[str, Any] | None,
    exploration: dict[str, Any] | None,
    existing_checks: list[str],
    valid_columns: set[str],
) -> list[dict[str, Any]]:
    """Returns normalized check proposal dicts (same shape as generator.heuristic_proposals)."""
    response = complete(
        system=prompts.CHECK_GEN_SYSTEM,
        user_prompt=prompts.check_gen_user_prompt(
            table_name, profile_summary, knowledge, exploration, existing_checks
        ),
        json_schema=CHECKS_SCHEMA,
        use_mcp=True,
    )
    data = parse_json_text(response.text)

    proposals: list[dict[str, Any]] = []
    for raw in data.get("checks", []):
        try:
            col = raw.get("column_name") or None
            if col and col not in valid_columns:
                raise ValueError(f"unknown column {col!r}")
            params = validate_check(raw["check_type"], col, raw.get("params") or {})
            minutes = int(raw.get("schedule_minutes") or 1440)
            proposals.append(
                {
                    "name": raw.get("name") or "",
                    "check_type": raw["check_type"],
                    "column_name": col,
                    "params": params,
                    "severity": raw.get("severity", "warn"),
                    "rationale": raw.get("rationale", ""),
                    "schedule_kind": "interval",
                    "schedule_expr": str(max(5, minutes)),
                }
            )
        except Exception as exc:  # noqa: BLE001 - skip invalid proposals, keep the rest
            log.warning("Dropping invalid LLM check proposal %r: %s", raw.get("name"), exc)
    return proposals
