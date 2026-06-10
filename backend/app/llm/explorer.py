"""Progressive data exploration agent: bounded read-only SQL loop -> insights."""

import logging
from typing import Any

from app.config import get_settings
from app.connectors.sa import Connector
from app.llm import prompts
from app.llm.client import (
    GET_TABLE_CODE_TOOL,
    RUN_SQL_TOOL,
    format_rows,
    redact_rows,
    run_agent_loop,
)

log = logging.getLogger(__name__)

SUBMIT_INSIGHTS_TOOL = {
    "name": "submit_insights",
    "description": "Submit your final data-quality insights for this table. Call exactly once, when done.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "insights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "detail": {"type": "string", "description": "Observed evidence with numbers"},
                        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                        "column": {"type": ["string", "null"]},
                        "suggested_check_type": {"type": ["string", "null"]},
                    },
                    "required": ["title", "detail", "risk", "column", "suggested_check_type"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["insights"],
        "additionalProperties": False,
    },
}


def explore_dataset(
    connector: Connector,
    table_ref: str,
    profile_summary: str,
    knowledge: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the exploration loop. Returns {"insights": [...], "queries_run": n, "transcript": [...]}."""
    settings = get_settings()
    pii = list((knowledge or {}).get("pii_columns") or [])
    transcript: list[dict[str, Any]] = []

    def execute_sql(inp: dict[str, Any]) -> str:
        res = connector.run_select(str(inp.get("sql", "")), limit=settings.agent_query_row_limit)
        rows = redact_rows(res.columns, res.rows, pii)
        return format_rows(res.columns, rows)

    def get_code(inp: dict[str, Any]) -> str:
        ddl, source = connector.get_ddl(str(inp.get("table", "")))
        return f"-- definition source: {source}\n{ddl}"

    result = run_agent_loop(
        system=prompts.EXPLORER_SYSTEM,
        user_prompt=prompts.explorer_user_prompt(table_ref, profile_summary, knowledge),
        handlers={"run_sql": execute_sql, "get_table_code": get_code},
        tools=[RUN_SQL_TOOL, GET_TABLE_CODE_TOOL],
        final_tool=SUBMIT_INSIGHTS_TOOL,
        max_turns=settings.llm_max_explore_turns,
        transcript=transcript,
    )
    queries_run = sum(1 for t in transcript if t["type"] == "sql")
    insights = (result or {}).get("insights", [])
    log.info("Explorer finished: %s insights from %s queries", len(insights), queries_run)
    return {"insights": insights, "queries_run": queries_run, "transcript": transcript}
