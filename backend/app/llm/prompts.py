"""Prompt templates and context builders for the LLM features.

When changing output contracts here, keep the parsers in check_gen/explorer/
rca_agent in sync.
"""

from typing import Any

from app.core.check_types import CHECK_TYPES


def check_types_doc() -> str:
    lines = []
    for ct in CHECK_TYPES.values():
        params = ", ".join(
            f"{p['name']}{'*' if p['required'] else ''}({p['type']})" for p in ct.params
        ) or "none"
        col = "column required" if ct.needs_column else "table-level"
        lines.append(f"- {ct.key}: {ct.description}. {col}. params: {params}")
    return "\n".join(lines)


def knowledge_block(knowledge: dict[str, Any] | None) -> str:
    if not knowledge:
        return "(no table knowledge recorded yet)"
    parts = []
    for key, label in [
        ("business_context", "Business context"),
        ("known_issues", "Known issues"),
        ("importance", "Importance"),
        ("owner", "Owner"),
        ("freshness_sla_hours", "Freshness SLA (hours)"),
        ("notes", "Notes"),
    ]:
        v = knowledge.get(key)
        if v not in (None, "", []):
            parts.append(f"{label}: {v}")
    if knowledge.get("pii_columns"):
        parts.append(f"PII columns (values are redacted for you): {knowledge['pii_columns']}")
    return "\n".join(parts) or "(no table knowledge recorded yet)"


CHECK_GEN_SYSTEM = """You are a senior data quality engineer. Given a table profile, table knowledge \
entered by the data team, and (optionally) insights from exploring the data, propose the data \
quality checks that would catch real problems in this table — especially in newly arriving data.

Guidelines:
- Propose checks that encode the table's actual contract, not generic boilerplate.
- Prefer precise, low-noise checks. A check that fires on existing legitimate data is a bad check; \
use observed bounds with sensible headroom, and use `tolerance` for known existing violations.
- Use the table knowledge: known issues deserve checks that detect recurrences; the freshness SLA \
sets freshness thresholds; importance scales severity.
- custom_sql is powerful: use it for cross-column rules (e.g. totals must equal sums of parts, \
status/date consistency, orphaned references). The query must return one row per violation.
- Severity: error = page someone / block usage; warn = investigate; info = monitor.
- Schedules: daily (1440 min) is the default; freshness checks should run every 360 min.
- 6-15 checks is the right range. Quality over quantity."""


EXPLORER_SYSTEM = """You are a data quality analyst exploring an unfamiliar table through read-only SQL \
to discover what could go wrong with this data. You have a limited number of queries — make each \
one count and prefer aggregates over raw row dumps.

Investigate progressively:
1. Start from the profile you were given — identify what's still unknown or suspicious.
2. Check distributions, ranges, and top values for columns that look risky.
3. Test cross-column consistency (totals vs parts, dates vs statuses, casing/format variants).
4. Look for duplicates, orphans, gaps in time series, magnitude outliers.

When you have a clear picture (or are running low on turns), call submit_insights with concrete, \
evidence-backed findings. Each insight should state what you observed (with numbers) and why it \
matters for data quality."""


RCA_SYSTEM = """You are a root-cause analyst for data quality incidents. A data quality check failed; \
your job is to find out WHY by investigating the source data with read-only SQL, then deliver a \
clear, evidence-backed report.

Method:
1. Reproduce: count and segment the violating rows yourself.
2. Localize: when did the bad rows start (by created/updated timestamps)? Which segments \
(category, source, country, status...) are affected? Is it concentrated or uniform?
3. Correlate: do the bad rows share other attributes? Do related tables explain them \
(missing parents, mismatched aggregates)?
4. Hypothesize: distinguish between (a) upstream pipeline bug, (b) source-system change, \
(c) legitimate business change, (d) a miscalibrated check.

Rules:
- Every claim in your report must be backed by a query you actually ran.
- Prefer aggregates; never dump large raw extracts.
- If the evidence is inconclusive, say so and report the most likely hypotheses with their evidence.
- When done, call submit_report. The report is markdown shown to data engineers: lead with the \
root cause summary, then evidence (include the key queries + result numbers), affected scope, \
and recommended fixes (both data fixes and check adjustments)."""


def check_gen_user_prompt(
    table_name: str,
    profile_summary: str,
    knowledge: dict[str, Any] | None,
    exploration: dict[str, Any] | None,
    existing_checks: list[str],
) -> str:
    parts = [
        f"Table: {table_name}",
        "",
        "## Profile",
        profile_summary,
        "",
        "## Table knowledge (from the data team)",
        knowledge_block(knowledge),
    ]
    if exploration and exploration.get("insights"):
        parts += ["", "## Exploration insights (from running SQL against this table)"]
        for ins in exploration["insights"]:
            parts.append(f"- {ins.get('title')}: {ins.get('detail')}")
    if existing_checks:
        parts += [
            "",
            "## Checks that already exist (do NOT propose duplicates)",
            "\n".join(f"- {c}" for c in existing_checks),
        ]
    parts += [
        "",
        "## Available check types",
        check_types_doc(),
        "",
        "Respond with the JSON object of proposed checks.",
    ]
    return "\n".join(parts)


def explorer_user_prompt(table_ref: str, profile_summary: str, knowledge: dict[str, Any] | None) -> str:
    return (
        f"Table to explore: {table_ref}\n\n"
        f"## Current profile\n{profile_summary}\n\n"
        f"## Table knowledge\n{knowledge_block(knowledge)}\n\n"
        "Explore the data and submit your data-quality insights."
    )


def rca_user_prompt(context: dict[str, Any]) -> str:
    parts = [
        f"Table: {context['table_ref']} (in {context['dialect']})",
        "",
        "## Failed check",
        f"name: {context['check_name']}",
        f"type: {context['check_type']}",
        f"column: {context.get('column') or '(table-level)'}",
        f"params: {context['params']}",
        f"run result: status={context['run_status']}, violations={context['violations']}, "
        f"metrics={context['metrics']}",
    ]
    if context.get("question"):
        parts += ["", "## Specific question from the user", context["question"]]
    if context.get("exception_samples"):
        parts += ["", "## Sample violating rows (truncated, PII-redacted)"]
        parts += [f"- {s}" for s in context["exception_samples"]]
    parts += [
        "",
        "## Profile summary",
        context["profile_summary"],
        "",
        "## Table knowledge",
        knowledge_block(context.get("knowledge")),
        "",
        "## Other tables visible in this source (you may query them for joins/context)",
        ", ".join(context.get("other_tables", [])) or "(none listed)",
        "",
        "Investigate and submit your root-cause report.",
    ]
    return "\n".join(parts)
