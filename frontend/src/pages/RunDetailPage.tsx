import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import type { Check, ExceptionRecord, Health, RcaSession, Run } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { checkTypeLabel } from "../lib/checkMeta";
import { fmtDateTime, fmtNum, fmtValue } from "../lib/format";
import { EmptyState, ErrorBox, Icon, Spinner, StatCard, StatusPill } from "../components/ui";

function metricValue(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (Array.isArray(value) || typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function rowPreview(row: Record<string, unknown>): string {
  return Object.entries(row)
    .slice(0, 6)
    .map(([k, v]) => `${k}=${fmtValue(v)}`)
    .join("  ");
}

function RunExceptionsTable({ exceptions }: { exceptions: ExceptionRecord[] }) {
  if (!exceptions.length) {
    return <EmptyState title="No captured exceptions" hint="This run did not persist violating row samples." />;
  }

  return (
    <div className="card table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Status</th>
            <th>Reason</th>
            <th>Row data</th>
            <th className="num">Score</th>
            <th>Seen</th>
          </tr>
        </thead>
        <tbody>
          {exceptions.map((e) => (
            <tr key={e.id}>
              <td>
                <StatusPill value={e.status} />
              </td>
              <td style={{ maxWidth: 320, fontWeight: 600, color: "var(--text-dark)" }}>{e.reason}</td>
              <td>
                <div className="rowdata">{rowPreview(e.row_data)}</div>
              </td>
              <td className="num">
                {e.outlier_score != null ? <span className="score-chip">{e.outlier_score}</span> : ""}
              </td>
              <td style={{ whiteSpace: "nowrap", color: "var(--text-light)", fontSize: 12 }}>
                {fmtDateTime(e.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function RunDetailPage() {
  const { id } = useParams();
  const runId = Number(id);
  const { user } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const runQuery = useQuery({
    queryKey: ["runs", runId],
    queryFn: () => api.get<Run>(`/runs/${runId}`),
    enabled: Number.isFinite(runId),
    refetchInterval: 20_000,
  });
  const run = runQuery.data;

  const checkQuery = useQuery({
    queryKey: ["checks", { datasetId: run?.dataset_id }],
    queryFn: () => api.get<Check[]>(`/checks?dataset_id=${run!.dataset_id}`),
    enabled: !!run,
  });
  const check = checkQuery.data?.find((c) => c.id === run?.check_id) ?? null;

  const exceptionsQuery = useQuery({
    queryKey: ["run-exceptions", runId],
    queryFn: () => api.get<ExceptionRecord[]>(`/runs/${runId}/exceptions`),
    enabled: Number.isFinite(runId),
  });

  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });

  const startRca = useMutation({
    mutationFn: () => api.post<RcaSession>("/rca/start", { check_run_id: runId }),
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: ["rca"] });
      navigate(`/datasets/${session.dataset_id}/rca`);
    },
  });

  if (runQuery.isLoading) return <Spinner label="Loading run..." />;
  if (runQuery.error) return <div className="page"><ErrorBox error={runQuery.error} /></div>;
  if (!run) return null;

  const metrics = Object.entries(run.metrics ?? {});
  const customSql = typeof check?.params?.sql === "string" ? check.params.sql : null;
  const canStartRca = canEdit(user) && health.data?.llm_enabled && ["fail", "warn", "error"].includes(run.status);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>
            Run #{run.id} <StatusPill value={run.status} />
          </h1>
          <div className="sub">
            <Link to={`/checks/${run.check_id}`}>{run.check_name}</Link> on{" "}
            <Link to={`/datasets/${run.dataset_id}`}>{run.dataset_name}</Link> · started {fmtDateTime(run.started_at)}
          </div>
        </div>
        <div className="header-actions">
          <Link to={`/checks/${run.check_id}`} className="btn">
            <Icon name="shield" size={13} /> Check
          </Link>
          <Link to={`/workbench?dataset_id=${run.dataset_id}&run_id=${run.id}`} className="btn">
            <Icon name="search" size={13} /> Open in Workbench
          </Link>
          <Link to={`/exceptions?run_id=${run.id}`} className="btn">
            <Icon name="alert" size={13} /> Triage exceptions
          </Link>
          {canEdit(user) && (
            <button
              className="primary"
              onClick={() => startRca.mutate()}
              disabled={!canStartRca || startRca.isPending}
              title={!health.data?.llm_enabled ? "Enable an LLM provider to run root-cause analysis" : undefined}
            >
              <Icon name="bolt" size={14} />
              {startRca.isPending ? "Starting RCA..." : "Start RCA"}
            </button>
          )}
        </div>
      </div>

      <ErrorBox error={startRca.error || checkQuery.error || exceptionsQuery.error} />

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <StatCard label="Violations" value={fmtNum(run.violation_count)} tone={run.violation_count ? "danger" : "ok"} />
        <StatCard label="Rows evaluated" value={fmtNum(run.rows_evaluated)} />
        <StatCard label="Exceptions" value={fmtNum(run.exception_count)} tone={run.exception_count ? "danger" : "ok"} />
        <StatCard label="Trigger" value={<span style={{ fontSize: 20 }}>{run.triggered_by}</span>} />
      </div>

      <div className="split" style={{ marginBottom: 16 }}>
        <div className="card card-pad">
          <h3>Run details</h3>
          <div className="table-wrap">
            <table className="data">
              <tbody>
                <tr>
                  <td style={{ fontWeight: 700, width: 150 }}>Check type</td>
                  <td>{checkTypeLabel(run.check_type)}</td>
                </tr>
                <tr>
                  <td style={{ fontWeight: 700 }}>Started</td>
                  <td>{fmtDateTime(run.started_at)}</td>
                </tr>
                <tr>
                  <td style={{ fontWeight: 700 }}>Finished</td>
                  <td>{fmtDateTime(run.finished_at)}</td>
                </tr>
                {run.error_message && (
                  <tr>
                    <td style={{ fontWeight: 700 }}>Error</td>
                    <td style={{ color: "var(--danger-dark)" }}>{run.error_message}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card card-pad">
          <h3>Metrics</h3>
          {!metrics.length ? (
            <EmptyState title="No metrics recorded" />
          ) : (
            <div className="table-wrap">
              <table className="data">
                <tbody>
                  {metrics.map(([k, v]) => (
                    <tr key={k}>
                      <td style={{ fontWeight: 700, width: 150 }}>{k}</td>
                      <td className="mono" style={{ overflowWrap: "anywhere" }}>{metricValue(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ margin: "0 0 10px" }}>
          <h2>Violation query</h2>
          <Link to={`/workbench?dataset_id=${run.dataset_id}&run_id=${run.id}`} className="btn small">
            Open in Workbench
          </Link>
        </div>
        {customSql ? (
          <pre className="sql">{customSql}</pre>
        ) : (
          <div style={{ color: "var(--text-light)", fontSize: 13 }}>
            This run uses the built-in {checkTypeLabel(run.check_type)} compiler. The compiled violation SQL is not stored
            with the run, but metrics and captured exceptions are persisted below.
          </div>
        )}
      </div>

      <div className="section-title">
        <h2>Exceptions</h2>
        <Link to={`/exceptions?run_id=${run.id}`} className="btn small">
          Triage this run
        </Link>
      </div>
      {exceptionsQuery.isLoading ? <Spinner /> : <RunExceptionsTable exceptions={exceptionsQuery.data ?? []} />}
    </div>
  );
}
