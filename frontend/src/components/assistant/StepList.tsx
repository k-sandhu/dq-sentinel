import type { ReactNode } from "react";

import type { ChatStep } from "../../api/types";
import ErrorBoundary from "../ErrorBoundary";
import Markdown from "../Markdown";
import PanelChart from "../PanelChart";
import { Icon } from "../ui";

// Authoring tools (#186) write config — surface them with a clear verb and open the
// confirmation by default; read tools stay collapsed.
const WRITE_TOOLS: Record<string, { icon: string; label: string }> = {
  create_check: { icon: "plus", label: "Created a check" },
  update_check: { icon: "settings", label: "Updated a check" },
  create_sla: { icon: "plus", label: "Created an SLA" },
  list_check_types: { icon: "book", label: "Looked up check types" },
};

/** The "act, not answer" step thread: text, SQL+result pairs, tool calls, charts, and
 *  errors. SQL/tool steps pair with their following result step into one collapsible. */
export function StepList({ steps }: { steps: ChatStep[] }) {
  const out: ReactNode[] = [];
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i];
    if (s.type === "text") {
      out.push(<Markdown key={i}>{s.content}</Markdown>);
    } else if (s.type === "sql") {
      // pair each query with its result in one collapsible
      const next = steps[i + 1];
      const result = next?.type === "result" ? next : null;
      if (result) i++;
      out.push(
        <details key={i} className="chat-activity">
          <summary>
            <Icon name="search" size={12} /> {s.purpose || "Ran a query"}
            {result?.error && <span className="badge danger">failed</span>}
          </summary>
          <pre className="sql">{s.sql}</pre>
          {result && (
            <pre className="result" style={result.error ? { borderColor: "var(--danger)", color: "var(--danger-dark)" } : undefined}>
              {result.content}
            </pre>
          )}
        </details>,
      );
    } else if (s.type === "result") {
      // orphan result (its sql was rendered in a previous batch) — show plainly
      out.push(
        <details key={i} className="chat-activity">
          <summary>{s.error ? "Query failed" : "Result"}</summary>
          <pre className="result">{s.content}</pre>
        </details>,
      );
    } else if (s.type === "tool") {
      const next = steps[i + 1];
      const result = next?.type === "result" ? next : null;
      if (result) i++;
      const meta = WRITE_TOOLS[s.name];
      out.push(
        <details key={i} className="chat-activity" open={!!meta && !result?.error}>
          <summary>
            <Icon name={meta?.icon ?? "book"} size={12} />{" "}
            {meta ? meta.label : `Looked at ${s.name.replace(/_/g, " ").replace(/^get /, "")}`}
            {result?.error && <span className="badge danger">failed</span>}
          </summary>
          {result && <pre className="result">{result.content}</pre>}
        </details>,
      );
    } else if (s.type === "chart") {
      out.push(
        <div key={i} className="chat-chart">
          <div className="chat-chart-title">{s.title}</div>
          <ErrorBoundary fallback={<div className="error-box">Could not render this chart.</div>}>
            <PanelChart columns={s.columns ?? []} rows={s.rows ?? []} viz={s.viz} height={220} />
          </ErrorBoundary>
        </div>,
      );
    } else if (s.type === "error") {
      out.push(
        <div key={i} className="error-box">
          {s.content}
        </div>,
      );
    }
  }
  return <>{out}</>;
}
