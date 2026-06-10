import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { Health, RcaSession, Run } from "../api/types";
import { canEdit, useAuth } from "../auth";
import RunsTable from "../components/RunsTable";
import { ErrorBox, Icon, Spinner } from "../components/ui";

const FILTERS = ["all", "fail", "warn", "error", "pass"] as const;

export default function RunsPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const { user } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const { data, isLoading, error } = useQuery({
    queryKey: ["runs", { filter }],
    queryFn: () => api.get<Run[]>(`/runs?limit=100${filter === "all" ? "" : `&status=${filter}`}`),
    refetchInterval: 20_000,
  });

  const startRca = useMutation({
    mutationFn: (run: Run) => api.post<RcaSession>("/rca/start", { check_run_id: run.id }),
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: ["rca"] });
      navigate(`/datasets/${session.dataset_id}/rca`);
    },
  });

  const failedRuns = (data ?? []).filter((r) => r.status === "fail" || r.status === "error");

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Runs</h1>
          <div className="sub">Check execution history (auto-refreshes)</div>
        </div>
        {canEdit(user) && health?.llm_enabled && failedRuns.length > 0 && (
          <button
            className="primary"
            onClick={() => startRca.mutate(failedRuns[0])}
            disabled={startRca.isPending}
            title={`Investigate the latest failure: ${failedRuns[0].check_name}`}
          >
            <Icon name="bolt" size={14} />
            {startRca.isPending ? "Starting agent…" : "Root-cause latest failure"}
          </button>
        )}
      </div>
      <div className="toolbar">
        <div className="chip-row">
          {FILTERS.map((f) => (
            <button key={f} className={`filter-chip${filter === f ? " on" : ""}`} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
      </div>
      <ErrorBox error={error || startRca.error} />
      {isLoading ? (
        <Spinner />
      ) : (
        <div className="card">
          <RunsTable runs={data ?? []} />
        </div>
      )}
    </div>
  );
}
