import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Health, RcaSession } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import RcaReport from "../../components/RcaReport";
import { EmptyState, ErrorBox, Icon, Spinner } from "../../components/ui";

export default function RcaTab({ datasetId }: { datasetId: number }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");

  const { data: health } = useQuery({ queryKey: qk.health.get(), queryFn: () => api.get<Health>("/health") });
  const llm = health?.llm_enabled ?? false;

  const { data: sessions, isLoading, error } = useQuery({
    queryKey: qk.rca.byDataset(datasetId),
    queryFn: () => api.get<RcaSession[]>(`/rca?dataset_id=${datasetId}`),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((s) => s.status === "running") ? 4_000 : false,
  });

  const start = useMutation({
    mutationFn: () => api.post<RcaSession>("/rca/start", { dataset_id: datasetId, question }),
    onSuccess: () => {
      setQuestion("");
      qc.invalidateQueries({ queryKey: qk.rca.byDataset(datasetId) });
    },
  });

  return (
    <div>
      {!llm && (
        <div className="info-box">
          Root-cause analysis needs an LLM. Set <code>DQ_LLM_API_KEY</code> + <code>DQ_LLM_MODEL</code> (OpenRouter or
          any OpenAI-compatible endpoint) or <code>ANTHROPIC_API_KEY</code> in the backend environment and restart.
        </div>
      )}
      {llm && canEdit(user) && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <h3>Start an investigation</h3>
          <p style={{ fontSize: 12.5, color: "var(--text-light)", marginTop: 0 }}>
            The agent investigates with read-only SQL (PII columns redacted) and returns an
            evidence-backed report. Tip: you can also start an RCA from a run's detail page — open a
            failed run and choose Start RCA.
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
              <Icon name="bolt" size={14} /> Run RCA
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
          <EmptyState title="No investigations yet" hint="Open a failed run and choose Start RCA, or ask a question above." />
        </div>
      ) : (
        sessions.map((s) => <RcaReport key={s.id} session={s} />)
      )}
    </div>
  );
}
