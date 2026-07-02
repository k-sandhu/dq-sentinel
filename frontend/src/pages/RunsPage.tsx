import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Health, RcaSession, Run } from "../api/types";
import { canEdit, useAuth } from "../auth";
import RunsTable from "../components/RunsTable";
import { ErrorBox, Icon, Spinner } from "../components/ui";

const FILTERS = ["all", "fail", "warn", "error", "pass"] as const;
type RunFilter = (typeof FILTERS)[number];

function asRunFilter(value: string | null): RunFilter {
  return FILTERS.includes(value as RunFilter) ? (value as RunFilter) : "all";
}

function sinceLabel(value: string): string {
  if (value === "24h") return "last 24h";
  if (value === "7d") return "last 7 days";
  if (value === "14d") return "last 14 days";
  return value;
}

export default function RunsPage() {
  const [params, setParams] = useSearchParams();
  const filter = asRunFilter(params.get("status"));
  const datasetId = params.get("dataset_id") ?? "";
  const checkId = params.get("check_id") ?? "";
  const runId = params.get("run_id") ?? "";
  const day = params.get("day") ?? "";
  const since = params.get("since") ?? "";
  const { user } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const apiParams = new URLSearchParams({ limit: "100" });
  if (filter !== "all") apiParams.set("status", filter);
  if (datasetId) apiParams.set("dataset_id", datasetId);
  if (checkId) apiParams.set("check_id", checkId);
  if (runId) apiParams.set("run_id", runId);
  if (day) apiParams.set("day", day);
  if (since) apiParams.set("since", since);
  const apiQuery = apiParams.toString();

  const patchParams = (mutate: (p: URLSearchParams) => void) => {
    const next = new URLSearchParams(params);
    mutate(next);
    setParams(next);
  };
  const setFilter = (nextFilter: RunFilter) =>
    patchParams((p) => {
      if (nextFilter === "all") p.delete("status");
      else p.set("status", nextFilter);
    });
  const clearParam = (key: string) => patchParams((p) => p.delete(key));

  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const { data, isLoading, error } = useQuery({
    queryKey: ["runs", apiQuery],
    queryFn: () => api.get<Run[]>(`/runs?${apiQuery}`),
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
  const activeFilters = [
    datasetId ? { key: "dataset_id", label: `dataset #${datasetId}` } : null,
    checkId ? { key: "check_id", label: `check #${checkId}` } : null,
    runId ? { key: "run_id", label: `run #${runId}` } : null,
    day ? { key: "day", label: day } : null,
    since ? { key: "since", label: sinceLabel(since) } : null,
  ].filter((f): f is { key: string; label: string } => f != null);

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
        <div className="right">
          {/* The `since` param existed but nothing set it — expose it so older runs
              beyond the latest-100 cap are reachable (#D7). */}
          <select
            aria-label="Time range"
            value={since}
            onChange={(e) => patchParams((p) => (e.target.value ? p.set("since", e.target.value) : p.delete("since")))}
          >
            <option value="">All time</option>
            <option value="24h">Last 24h</option>
            <option value="7d">Last 7 days</option>
            <option value="14d">Last 14 days</option>
          </select>
        </div>
      </div>
      {activeFilters.length > 0 && (
        <div className="active-filters">
          {activeFilters.map((f) => (
            <span className="filter-tag" key={f.key}>
              {f.label}
              <button
                type="button"
                className="tag-x"
                aria-label={`Clear ${f.label} filter`}
                onClick={() => clearParam(f.key)}
              >
                &times;
              </button>
            </span>
          ))}
        </div>
      )}
      <ErrorBox error={error || startRca.error} />
      {isLoading ? (
        <Spinner />
      ) : (
        <div className="card">
          <RunsTable runs={data ?? []} />
          {(data?.length ?? 0) >= 100 && (
            <div className="sub" style={{ padding: "8px 12px", borderTop: "1px solid var(--border)" }}>
              Showing the latest 100 runs. Narrow by status, dataset, or time range to see older runs.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
