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
from app.llm.client import format_rows, redact_rows, run_agent_loop
from app.models import Check, CheckRun, Dataset, ExceptionRecord, Profile, RcaSession, utcnow

log = logging.getLogger(__name__)

SUBMIT_REPORT_TOOL = {
    "name": "submit_report",
    "description": "Submit your final root-cause analysis report. Call exactly once, when done.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "root_cause_summary": {"type": "string", "description": "1-3 sentences, plain language"},
            "report_md": {"type": "string", "description": "Full markdown report with evidence"},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "suggested_fixes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["root_cause_summary", "report_md", "confidence", "suggested_fixes"],
        "additionalProperties": False,
    },
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

            def execute_sql(sql: str) -> str:
                res = connector.run_select(sql, limit=settings.agent_query_row_limit)
                return format_rows(res.columns, redact_rows(res.columns, res.rows, pii))

            result = run_agent_loop(
                system=prompts.RCA_SYSTEM,
                user_prompt=prompts.rca_user_prompt(ctx),
                execute_sql=execute_sql,
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
            else:
                session.status = "failed"
                session.report_md = "The agent did not produce a report within its turn limit."
        except Exception as exc:  # noqa: BLE001 - persist the failure for the UI
            log.exception("RCA session %s failed", session_id)
            session.status = "failed"
            session.report_md = f"RCA failed: {type(exc).__name__}: {exc}"
        session.finished_at = utcnow()
        db.commit()
