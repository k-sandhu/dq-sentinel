import { useRef, useState } from "react";
import { Link } from "react-router";
import type { RcaAction, RcaEvidence, RcaHypothesis, RcaReport as RcaReportShape, RcaSession, TranscriptStep } from "../api/types";
import { fmtDateTime } from "../lib/format";
import { rcaToMarkdown } from "../lib/rcaExport";
import Markdown from "./Markdown";
import { Icon, Modal, Spinner, StatusPill } from "./ui";

function Transcript({ steps }: { steps: TranscriptStep[] }) {
  const sqlSteps = steps.filter((s) => s.type === "sql").length;
  return (
    <details className="step" style={{ marginTop: 14 }}>
      <summary>Investigation transcript ({sqlSteps} queries)</summary>
      <div className="body">
        {steps.map((s, i) => {
          if (s.type === "text") {
            return (
              <p key={i} style={{ fontSize: 12.5, margin: "8px 0" }}>
                {String(s.content)}
              </p>
            );
          }
          if (s.type === "sql") {
            return (
              <div key={i}>
                {s.purpose && <div style={{ fontSize: 12, fontWeight: 700, marginTop: 8 }}>{"\u25b8 "}{s.purpose}</div>}
                <pre className="sql">{s.sql}</pre>
              </div>
            );
          }
          if (s.type === "result") {
            return (
              <pre key={i} className="result" style={s.error ? { borderColor: "var(--danger)", color: "var(--danger-dark)" } : undefined}>
                {String(s.content)}
              </pre>
            );
          }
          return null;
        })}
      </div>
    </details>
  );
}

async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to the textarea copy path for locked-down browsers.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.select();

  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
  }
}

function ConfidencePill({ confidence }: { confidence: RcaReportShape["confidence"] }) {
  const tone = confidence === "high" ? "ok" : confidence === "medium" ? "info" : confidence === "low" ? "warn" : "neutral";
  const className = `pill tone-${tone}`;
  return <span className={className}>{confidence ? `${confidence} confidence` : "unknown confidence"}</span>;
}

function VerdictPill({ verdict }: { verdict: RcaHypothesis["verdict"] }) {
  const tone = verdict === "supported" ? "ok" : verdict === "inconclusive" ? "warn" : "neutral";
  const className = `pill tone-${tone}`;
  return <span className={className}>{verdict}</span>;
}

function transcriptResultForSql(steps: TranscriptStep[], sql: string): TranscriptStep | null {
  const target = sql.trim();
  if (!target) return null;
  for (let i = 0; i < steps.length; i += 1) {
    const step = steps[i];
    if (step.type === "sql" && (step.sql ?? "").trim() === target) {
      for (let j = i + 1; j < steps.length; j += 1) {
        if (steps[j].type === "result") return steps[j];
        if (steps[j].type === "sql") break;
      }
    }
  }
  return null;
}

function ExportToolbar({ session }: { session: RcaSession }) {
  const [copied, setCopied] = useState(false);
  const [manualMarkdown, setManualMarkdown] = useState("");
  const manualRef = useRef<HTMLTextAreaElement | null>(null);
  const markdown = rcaToMarkdown(session);

  async function copyMarkdown() {
    const ok = await copyText(markdown);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } else {
      setManualMarkdown(markdown);
      window.setTimeout(() => manualRef.current?.select(), 0);
    }
  }

  function downloadMarkdown() {
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `rca-${session.id}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return (
    <>
      <div className="toolbar rca-toolbar">
        <div className="right">
          <button className="small" onClick={copyMarkdown} title="Copy report as Markdown">
            <Icon name={copied ? "check" : "copy"} size={12} /> {copied ? "Copied" : "Copy as Markdown"}
          </button>
          <button className="small" onClick={downloadMarkdown}>
            Download .md
          </button>
        </div>
      </div>
      {manualMarkdown && (
        <Modal
          title="Copy markdown manually"
          onClose={() => setManualMarkdown("")}
          wide
          footer={
            <>
              <button onClick={() => manualRef.current?.select()}>Select all</button>
              <button className="primary" onClick={() => setManualMarkdown("")}>Close</button>
            </>
          }
        >
          <p style={{ marginTop: 0, color: "var(--text-light)", fontSize: 12.5 }}>
            Clipboard access is blocked in this browser. Select all and copy the markdown manually.
          </p>
          <textarea ref={manualRef} readOnly value={manualMarkdown} className="rca-copy-textarea" onFocus={(e) => e.currentTarget.select()} />
        </Modal>
      )}
    </>
  );
}

function HypothesesTable({ hypotheses }: { hypotheses: RcaHypothesis[] }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Statement</th>
            <th>Verdict</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {hypotheses.map((hypothesis, index) => (
            <tr key={`${hypothesis.statement}-${index}`}>
              <td>{hypothesis.statement}</td>
              <td><VerdictPill verdict={hypothesis.verdict} /></td>
              <td>{hypothesis.evidence}</td>
            </tr>
          ))}
          {hypotheses.length === 0 && (
            <tr>
              <td colSpan={3} style={{ color: "var(--text-light)" }}>No hypotheses reported.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function EvidenceItem({ item, result }: { item: RcaEvidence; result: TranscriptStep | null }) {
  const [copied, setCopied] = useState(false);

  async function copySql() {
    const ok = await copyText(item.sql);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    }
  }

  return (
    <details className="step">
      <summary>{"\u25b8 "}{item.title}</summary>
      <div className="body">
        {item.sql.trim() && <pre className="sql">{item.sql}</pre>}
        {item.finding.trim() && <p className="rca-finding">{item.finding}</p>}
        <button className="small" onClick={copySql} disabled={!item.sql.trim()}>
          <Icon name={copied ? "check" : "copy"} size={12} /> {copied ? "Copied" : "Copy SQL"}
        </button>
        {result && (
          <pre className="result" style={result.error ? { borderColor: "var(--danger)", color: "var(--danger-dark)" } : undefined}>
            {String(result.content)}
          </pre>
        )}
      </div>
    </details>
  );
}

function EvidenceList({ evidence, transcript }: { evidence: RcaEvidence[]; transcript: TranscriptStep[] }) {
  if (evidence.length === 0) return <p className="rca-muted">No structured evidence reported.</p>;
  return (
    <div className="rca-evidence-list">
      {evidence.map((item, index) => (
        <EvidenceItem key={`${item.title}-${index}`} item={item} result={transcriptResultForSql(transcript, item.sql)} />
      ))}
    </div>
  );
}

function ActionsList({ actions, session }: { actions: RcaAction[]; session: RcaSession }) {
  if (actions.length === 0) return <p className="rca-muted">No recommended actions reported.</p>;
  return (
    <div className="rca-action-list">
      {actions.map((item, index) => (
        <div className="rca-action-row" key={`${item.kind}-${item.action}-${index}`}>
          {/* A bullet, not a disabled checkbox: these are recommendations to read,
              not an interactive task list (#D16). */}
          <span className="rca-action-bullet" aria-hidden="true">•</span>
          <span className="badge kind">{item.kind}</span>
          <span className="rca-action-text">{item.action}</span>
          {item.kind === "adjust_check" && session.check_run_id && (
            <Link to={`/datasets/${session.dataset_id}/checks`} className="btn small">
              Checks tab
            </Link>
          )}
        </div>
      ))}
    </div>
  );
}

function StructuredReport({ session, report }: { session: RcaSession; report: RcaReportShape }) {
  const hypotheses = Array.isArray(report.hypotheses) ? report.hypotheses : [];
  const evidence = Array.isArray(report.evidence) ? report.evidence : [];
  const actions = Array.isArray(report.recommended_actions) ? report.recommended_actions : [];
  const hasUnknownVersion = report.version !== undefined && report.version !== 1;

  return (
    <>
      {hasUnknownVersion && (
        <div className="info-box rca-format-note">report format v{report.version} - rendering best-effort</div>
      )}
      <div className="info-box rca-verdict">
        <strong>{session.root_cause_summary || "No summary provided."}</strong>
        <ConfidencePill confidence={report.confidence} />
      </div>
      {report.likely_cause?.trim() && (
        <div className="rca-likely-cause">
          <strong>Likely cause:</strong> {report.likely_cause}
        </div>
      )}

      <section className="rca-section">
        <h4>Hypotheses</h4>
        <HypothesesTable hypotheses={hypotheses} />
      </section>

      <section className="rca-section">
        <h4>Evidence</h4>
        <EvidenceList evidence={evidence} transcript={session.transcript ?? []} />
      </section>

      <section className="rca-section">
        <h4>Recommended actions</h4>
        <ActionsList actions={actions} session={session} />
      </section>
    </>
  );
}

function LegacyReport({ session }: { session: RcaSession }) {
  return (
    <>
      {session.root_cause_summary && (
        <div className="info-box" style={{ fontWeight: 600 }}>{session.root_cause_summary}</div>
      )}
      <Markdown>{session.report_md}</Markdown>
    </>
  );
}

export default function RcaReport({ session }: { session: RcaSession }) {
  const report = session.report_json ?? null;

  return (
    <div className="card card-pad" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <h3 style={{ marginBottom: 4 }}>
          <StatusPill value={session.status} />{" "}
          {session.check_run_id ? `Run #${session.check_run_id}` : "Ad-hoc investigation"}
          <span style={{ fontWeight: 400, color: "var(--text-light)", fontSize: 12, marginLeft: 8 }}>
            {fmtDateTime(session.created_at)} {session.model && `\u00b7 ${session.model}`}
          </span>
        </h3>
      </div>
      {session.question && (
        <p style={{ fontSize: 12.5, color: "var(--text-light)", margin: "2px 0 8px" }}>
          Q: {session.question}
        </p>
      )}

      {session.status === "complete" && <ExportToolbar session={session} />}

      {session.status === "running" ? (
        <>
          <Spinner label="Agent is investigating (writing read-only SQL against the source)..." />
          {session.transcript?.length > 0 && <Transcript steps={session.transcript} />}
        </>
      ) : session.status === "failed" ? (
        <>
          <div className="error-box">{session.report_md || session.root_cause_summary || "Investigation failed."}</div>
          {session.transcript?.length > 0 && <Transcript steps={session.transcript} />}
        </>
      ) : (
        <>
          {report ? <StructuredReport session={session} report={report} /> : <LegacyReport session={session} />}
          {session.transcript?.length > 0 && <Transcript steps={session.transcript} />}
        </>
      )}
    </div>
  );
}
