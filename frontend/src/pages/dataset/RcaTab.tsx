import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "../../api/client";
import type { Health, RcaSession, TranscriptStep } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import { EmptyState, ErrorBox, Icon, Pill, Spinner } from "../../components/ui";
import { fmtDateTime } from "../../lib/format";

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
                {s.purpose && <div style={{ fontSize: 12, fontWeight: 700, marginTop: 8 }}>▸ {s.purpose}</div>}
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

function SessionView({ session }: { session: RcaSession }) {
  return (
    <div className="card card-pad" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <h3 style={{ marginBottom: 4 }}>
          <Pill value={session.status} />{" "}
          {session.check_run_id ? `Run #${session.check_run_id}` : "Ad-hoc investigation"}
          <span style={{ fontWeight: 400, color: "var(--text-light)", fontSize: 12, marginLeft: 8 }}>
            {fmtDateTime(session.created_at)} {session.model && `· ${session.model}`}
          </span>
        </h3>
      </div>
      {session.question && (
        <p style={{ fontSize: 12.5, color: "var(--text-light)", margin: "2px 0 8px" }}>
          Q: {session.question}
        </p>
      )}
      {session.status === "running" ? (
        <Spinner label="Agent is investigating (writing read-only SQL against the source)…" />
      ) : (
        <>
          {session.root_cause_summary && (
            <div className="info-box" style={{ fontWeight: 600 }}>{session.root_cause_summary}</div>
          )}
          <div className="markdown">
            <ReactMarkdown>{session.report_md}</ReactMarkdown>
          </div>
          {session.transcript?.length > 0 && <Transcript steps={session.transcript} />}
        </>
      )}
    </div>
  );
}

export default function RcaTab({ datasetId }: { datasetId: number }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");

  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const llm = health?.llm_enabled ?? false;

  const { data: sessions, isLoading, error } = useQuery({
    queryKey: ["rca", datasetId],
    queryFn: () => api.get<RcaSession[]>(`/rca?dataset_id=${datasetId}`),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((s) => s.status === "running") ? 4_000 : false,
  });

  const start = useMutation({
    mutationFn: () => api.post<RcaSession>("/rca/start", { dataset_id: datasetId, question }),
    onSuccess: () => {
      setQuestion("");
      qc.invalidateQueries({ queryKey: ["rca", datasetId] });
    },
  });

  return (
    <div>
      {!llm && (
        <div className="info-box">
          Root-cause analysis needs an LLM. Set <code>ANTHROPIC_API_KEY</code> in the backend environment and restart.
        </div>
      )}
      {llm && canEdit(user) && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <h3>Start an investigation</h3>
          <p style={{ fontSize: 12.5, color: "var(--text-light)", marginTop: 0 }}>
            The agent investigates with read-only SQL (PII columns redacted) and returns an
            evidence-backed report. Tip: you can also launch an RCA from any failed run on the Runs tab.
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              type="text"
              placeholder='e.g. "Why are there negative totals in recent orders?"'
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              style={{ marginTop: 0, flex: 1 }}
            />
            <button className="primary" onClick={() => start.mutate()} disabled={!question.trim() || start.isPending}>
              <Icon name="bolt" size={14} /> Investigate
            </button>
          </div>
          <ErrorBox error={start.error} />
        </div>
      )}
      <ErrorBox error={error} />
      {isLoading ? (
        <Spinner />
      ) : !sessions?.length ? (
        <div className="card">
          <EmptyState title="No investigations yet" hint="Launch one from a failed run, or ask a question above." />
        </div>
      ) : (
        sessions.map((s) => <SessionView key={s.id} session={s} />)
      )}
    </div>
  );
}
