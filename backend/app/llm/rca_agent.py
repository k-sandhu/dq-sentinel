"""Agentic root-cause analysis for failed check runs.

Runs in a background task: loads the failure context, lets the model investigate
with read-only SQL, and persists an evidence-backed markdown report + transcript.
"""

import json
import logging
from typing import Any

from app.config import get_settings
from app.connectors.sa import connector_for
from app.core.profiler import summarize_profile_for_llm
from app.db import session_factory
from app.llm import prompts
from app.llm.client import (
    GET_TABLE_CODE_TOOL,
    RUN_SQL_TOOL,
    format_rows,
    parse_json_text,
    redact_rows,
    run_agent_loop,
    safe_user_error,
)
from app.models import Check, CheckRun, Dataset, ExceptionRecord, Profile, RcaSession, utcnow

log = logging.getLogger(__name__)

# Structured-report contract (#287). Field names/enums are the wire contract with
# frontend/src/api/types.ts (RcaReport/RcaHypothesis/RcaEvidence/RcaAction) — keep in
# sync. Only the legacy four fields are `required`, so a weaker model that ignores the
# structured half still submits a usable markdown report (report_json stays NULL).
REPORT_VERSION = 1
VERDICTS = ("supported", "refuted", "inconclusive")
ACTION_KINDS = ("fix_data", "fix_pipeline", "adjust_check", "investigate")
CONFIDENCES = ("low", "medium", "high")

SUBMIT_REPORT_TOOL = {
    "name": "submit_report",
    "description": "Submit your final root-cause analysis report. Call exactly once, when done.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "root_cause_summary": {"type": "string", "description": "1-3 sentences, plain language"},
            "report_md": {"type": "string", "description": "Full markdown report with evidence"},
            "confidence": {"type": "string", "enum": list(CONFIDENCES)},
            "suggested_fixes": {"type": "array", "items": {"type": "string"}},
            "likely_cause": {
                "type": "string",
                "description": "One sentence naming the single most likely cause.",
            },
            "hypotheses": {
                "type": "array",
                "description": "Every hypothesis you tested, including the ones you ruled out.",
                "items": {
                    "type": "object",
                    "properties": {
                        "statement": {"type": "string", "description": "The hypothesis, one line"},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "evidence": {
                            "type": "string",
                            "description": "The numbers that settled it, one line",
                        },
                    },
                    "required": ["statement", "verdict"],
                    "additionalProperties": False,
                },
            },
            "evidence": {
                "type": "array",
                "description": "The key queries you actually ran, with what each showed.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "What this query establishes"},
                        "sql": {
                            "type": "string",
                            "description": "The query verbatim as you ran it via run_sql",
                        },
                        "finding": {"type": "string", "description": "What the result showed"},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
            },
            "recommended_actions": {
                "type": "array",
                "description": "Concrete next steps, most important first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "One concrete step"},
                        "kind": {"type": "string", "enum": list(ACTION_KINDS)},
                    },
                    "required": ["action", "kind"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["root_cause_summary", "report_md", "confidence", "suggested_fixes"],
        "additionalProperties": False,
    },
}


def _text(value: Any) -> str:
    """Model fields arrive as strings; tolerate the odd number/None."""
    if value is None:
        return ""
    return value.strip() if isinstance(value, str) else str(value)


def _as_list(value: Any) -> list[Any]:
    """A list, or a (possibly markdown-fenced) JSON string holding one."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = parse_json_text(value)
        except Exception:  # noqa: BLE001 - malformed structure is not fatal
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = parse_json_text(value)
        except Exception:  # noqa: BLE001
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _enum(value: Any, allowed: tuple[str, ...], fallback: str | None) -> str | None:
    text = _text(value).lower()
    return text if text in allowed else fallback


def build_report_json(result: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize the agent's submit_report input into the stored/served structured
    report. Returns None when the model produced nothing structured — the markdown
    report then remains the whole story (graceful degradation)."""
    hypotheses = [
        {
            "statement": _text(item.get("statement")),
            # An unrecognized verdict is 'we could not settle it', never 'supported'.
            "verdict": _enum(item.get("verdict"), VERDICTS, "inconclusive"),
            "evidence": _text(item.get("evidence")),
        }
        for item in (_as_dict(raw) for raw in _as_list(result.get("hypotheses")))
        if _text(item.get("statement"))
    ]
    evidence = [
        {
            "title": _text(item.get("title")),
            "sql": _text(item.get("sql")),
            "finding": _text(item.get("finding")),
        }
        for item in (_as_dict(raw) for raw in _as_list(result.get("evidence")))
        if _text(item.get("title"))
    ]
    actions = [
        {
            "action": _text(item.get("action")),
            "kind": _enum(item.get("kind"), ACTION_KINDS, "investigate"),
        }
        for item in (_as_dict(raw) for raw in _as_list(result.get("recommended_actions")))
        if _text(item.get("action"))
    ]
    likely_cause = _text(result.get("likely_cause"))
    if not (hypotheses or evidence or actions or likely_cause):
        return None
    return {
        "version": REPORT_VERSION,
        "hypotheses": hypotheses,
        "evidence": evidence,
        "likely_cause": likely_cause,
        "recommended_actions": actions,
        "confidence": _enum(result.get("confidence"), CONFIDENCES, None),
    }


def _build_context(db, session: RcaSession) -> dict[str, Any]:
    dataset: Dataset = db.get(Dataset, session.dataset_id)
    connector = connector_for(dataset.connection)
    ctx: dict[str, Any] = {
        "table_ref": connector.table_ref(dataset.table_name, dataset.schema_name),
        "dialect": connector.kind,
        "question": session.question,
        "check_name": "(ad-hoc investigation)",
        "check_type": "-",
        "column": None,
        "params": {},
        "run_status": "-",
        "violations": "-",
        "metrics": {},
    }

    knowledge = dataset.knowledge
    ctx["knowledge"] = (
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
    )

    profile = (
        db.query(Profile).filter(Profile.dataset_id == dataset.id).order_by(Profile.id.desc()).first()
    )
    ctx["profile_summary"] = (
        summarize_profile_for_llm(
            {"row_count": profile.row_count, "sampled_rows": profile.sampled_rows,
             "columns": profile.columns, "table_facts": profile.table_facts},
            (ctx["knowledge"] or {}).get("pii_columns") if ctx["knowledge"] else None,
        )
        if profile
        else "(table has not been profiled yet)"
    )

    if session.check_run_id:
        run: CheckRun = db.get(CheckRun, session.check_run_id)
        check: Check = db.get(Check, run.check_id)
        ctx.update(
            check_name=check.name,
            check_type=check.check_type,
            column=check.column_name,
            params=check.params,
            run_status=run.status,
            violations=run.violation_count,
            metrics=run.metrics,
        )
        pii = {c.lower() for c in ((ctx["knowledge"] or {}).get("pii_columns") or [])}
        samples = (
            db.query(ExceptionRecord).filter(ExceptionRecord.run_id == run.id).limit(8).all()
        )
        ctx["exception_samples"] = [
            json.dumps(
                {k: ("[REDACTED]" if k.lower() in pii else v) for k, v in (e.row_data or {}).items()},
                default=str,
            )[:400]
            for e in samples
        ]

    try:
        ctx["other_tables"] = [t["table_name"] for t in connector.list_tables()][:40]
    except Exception:  # noqa: BLE001
        ctx["other_tables"] = []
    return ctx


def run_rca_session(session_id: int) -> None:
    """Background task entrypoint. Creates its own DB session."""
    settings = get_settings()
    factory = session_factory()
    with factory() as db:
        session = db.get(RcaSession, session_id)
        if session is None:
            return
        try:
            dataset = db.get(Dataset, session.dataset_id)
            connector = connector_for(dataset.connection)
            ctx = _build_context(db, session)
            pii = list(((ctx.get("knowledge") or {}).get("pii_columns")) or [])
            transcript: list[dict[str, Any]] = []

            def execute_sql(inp: dict[str, Any]) -> str:
                res = connector.run_select(
                    str(inp.get("sql", "")), limit=settings.agent_query_row_limit
                )
                return format_rows(res.columns, redact_rows(res.columns, res.rows, pii))

            def get_code(inp: dict[str, Any]) -> str:
                ddl, source = connector.get_ddl(str(inp.get("table", "")))
                return f"-- definition source: {source}\n{ddl}"

            result = run_agent_loop(
                system=prompts.RCA_SYSTEM,
                user_prompt=prompts.rca_user_prompt(ctx),
                handlers={"run_sql": execute_sql, "get_table_code": get_code},
                tools=[RUN_SQL_TOOL, GET_TABLE_CODE_TOOL],
                final_tool=SUBMIT_REPORT_TOOL,
                max_turns=settings.llm_max_rca_turns,
                transcript=transcript,
            )

            session.transcript = transcript
            session.model = settings.llm_model
            if result:
                session.status = "complete"
                session.root_cause_summary = result.get("root_cause_summary", "")
                fixes = result.get("suggested_fixes") or []
                report = result.get("report_md", "")
                if fixes and "## Suggested fixes" not in report:
                    report += "\n\n## Suggested fixes\n" + "\n".join(f"- {f}" for f in fixes)
                session.report_md = report
                # None when the model skipped the structured half; report_md stands alone.
                session.report_json = build_report_json(result)
            else:
                session.status = "failed"
                session.report_md = "The agent did not produce a report within its turn limit."
        except Exception as exc:  # noqa: BLE001 - persist the failure for the UI
            log.exception("RCA session %s failed", session_id)
            session.status = "failed"
            session.report_md = f"The investigation could not finish: {safe_user_error(exc)}"
        session.finished_at = utcnow()
        db.commit()
