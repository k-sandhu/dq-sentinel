import type { RcaSession } from "../api/types";

function parseUtc(iso: string): Date | null {
  const value = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return Number.isNaN(value.getTime()) ? null : value;
}

function utcStamp(iso: string): string {
  const value = parseUtc(iso);
  if (!value) return `${iso} UTC`;
  return value.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC");
}

function tableCell(value: string): string {
  return value.replace(/\|/g, "\\|").replace(/\r?\n/g, "<br>");
}

function pushIfPresent(lines: string[], value: string | undefined) {
  if (value?.trim()) lines.push(value.trim());
}

export function rcaToMarkdown(session: RcaSession): string {
  const report = session.report_json;
  if (!report) return session.report_md;

  const createdAt = utcStamp(session.created_at);
  const scope = session.check_run_id ? `run #${session.check_run_id}` : "ad-hoc";
  const confidence = report.confidence ?? "unknown";
  const model = session.model || "unknown";
  const lines: string[] = [
    `# RCA: ${session.dataset_name} - ${scope} (${createdAt})`,
    `**Confidence:** ${confidence} | **Model:** ${model}`,
  ];

  if (session.check_run_id) {
    lines.push(`**Run:** #${session.check_run_id}`);
  } else if (session.question.trim()) {
    lines.push(`**Question:** ${session.question.trim()}`);
  }

  if (report.version !== undefined && report.version !== 1) {
    lines.push("", `> Report format v${report.version} - rendering best-effort.`);
  }

  lines.push("", "## Summary");
  pushIfPresent(lines, session.root_cause_summary || "No summary provided.");

  if (report.likely_cause?.trim()) {
    lines.push("", `**Likely cause:** ${report.likely_cause.trim()}`);
  }

  const hypotheses = Array.isArray(report.hypotheses) ? report.hypotheses : [];
  if (hypotheses.length > 0) {
    lines.push("", "## Hypotheses", "| Hypothesis | Verdict | Evidence |", "|---|---|---|");
    hypotheses.forEach((hypothesis) => {
      lines.push(
        `| ${tableCell(hypothesis.statement)} | ${tableCell(hypothesis.verdict)} | ${tableCell(hypothesis.evidence)} |`,
      );
    });
  }

  const evidence = Array.isArray(report.evidence) ? report.evidence : [];
  if (evidence.length > 0) {
    lines.push("", "## Evidence");
    evidence.forEach((item) => {
      lines.push("", `### ${item.title}`);
      if (item.sql.trim()) {
        lines.push("```sql", item.sql.trim(), "```");
      }
      pushIfPresent(lines, item.finding);
    });
  }

  const actions = Array.isArray(report.recommended_actions) ? report.recommended_actions : [];
  if (actions.length > 0) {
    lines.push("", "## Recommended actions");
    actions.forEach((item) => {
      lines.push(`- [${item.kind}] ${item.action}`);
    });
  }

  return `${lines.join("\n")}\n`;
}
