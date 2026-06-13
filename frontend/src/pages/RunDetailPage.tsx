import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import type { ExceptionRecord, Health, RcaSession, Run } from "../api/types";
import { canEdit, useAuth } from "../auth";
import Breadcrumbs from "../components/Breadcrumbs";
import { EmptyState, ErrorBox, Icon, Pill, Spinner, StatCard } from "../components/ui";
import { checkTypeLabel } from "../lib/checkMeta";
import { fmtDateTime, fmtNum, fmtValue } from "../lib/format";

export default function RunDetailPage() {
  const { id } = useParams();
  const runId = Number(id);
  const { user } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const runQuery = useQuery({
    queryKey: ["runs", runId],
    queryFn: () => api.get<Run>(`/runs/${runId}`),
  });
  const exceptionsQuery = useQuery({
    queryKey: ["exceptions", { runId }],
    queryFn: () => api.get<ExceptionRecord[]>(`/runs/${runId}/exceptions`),
  });

  const startRca = useMutation({
    mutationFn: () => api.post<RcaSession>("/rca/start", { check_run_id: runId }),
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: ["rca"] });
      navigate(`/datasets/${session.dataset_id}/rca`);
    },
  });

  const run = runQuery.data;
  if (runQuery.isLoading) return <Spinner label="Loading run..." />;

  return (
    <div className="page">
      <Breadcrumbs items={[{ label: "Runs", to: "/runs" }, { label: `Run #${runId}` }]} />
      <ErrorBox error={runQuery.error || exceptionsQuery.error || startRca.error} />
      {!run ? (
        <div className="card">
          <EmptyState title="Run not found" />
        </div>
      ) : (
        <>
          <div className="page-header">
            <div>
              <h1>
                Run #{run.id} <Pill value={run.status} />
              </h1>
              <div className="sub">
                <Link to={`/checks/${run.check_id}`}>{run.check_name}</Link> on{" "}
                <Link to={`/datasets/${run.dataset_id}`}>{run.dataset_name}</Link> · {checkTypeLabel(run.check_type)}
              </div>
            </div>
            <div className="header-actions">
              <Link to={`/workbench?dataset_id=${run.dataset_id}&run_id=${run.id}`} className="btn">
                <Icon name="search" size={13} /> Investigate
              </Link>
              {canEdit(user) && health?.llm_enabled && (
                <button className="primary" onClick={() => startRca.mutate()} disabled={startRca.isPending}>
                  <Icon name="bolt" size={14} />
                  {startRca.isPending ? "Starting RCA..." : "Start RCA"}
                </button>
              )}
            </div>
          </div>

          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <StatCard label="Violations" value={fmtNum(run.violation_count)} tone={run.violation_count ? "danger" : "ok"} />
            <StatCard label="Exceptions" value={fmtNum(run.exception_count)} />
            <StatCard label="Rows evaluated" value={fmtNum(run.rows_evaluated)} />
            <StatCard label="Trigger" value={<span className="badge">{run.triggered_by}</span>} />
          </div>

          <div className="grid cols-2">
            <div className="card card-pad">
              <h3>Run Details</h3>
              <dl className="detail-list">
                <dt>Started</dt>
                <dd>{fmtDateTime(run.started_at)}</dd>
                <dt>Finished</dt>
                <dd>{run.finished_at ? fmtDateTime(run.finished_at) : "Still running"}</dd>
                <dt>Check type</dt>
                <dd>{checkTypeLabel(run.check_type)}</dd>
                {run.error_message && (
                  <>
                    <dt>Error</dt>
                    <dd className="danger-text">{run.error_message}</dd>
                  </>
                )}
              </dl>
            </div>
            <div className="card card-pad">
              <h3>Metrics</h3>
              <pre className="result">{JSON.stringify(run.metrics ?? {}, null, 2)}</pre>
            </div>
          </div>

          <div className="section-title">
            <h2>Exceptions</h2>
            <Link to={`/exceptions?run_id=${run.id}`}>Open in triage →</Link>
          </div>
          {!exceptionsQuery.data?.length ? (
            <div className="card">
              <EmptyState title="No exceptions captured for this run" />
            </div>
          ) : (
            <div className="card table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Reason</th>
                    <th>Row data</th>
                    <th>Seen</th>
                  </tr>
                </thead>
                <tbody>
                  {exceptionsQuery.data.map((exc) => (
                    <tr key={exc.id}>
                      <td><Pill value={exc.status} /></td>
                      <td style={{ fontWeight: 600, color: "var(--text-dark)" }}>{exc.reason}</td>
                      <td>
                        <div className="rowdata">
                          {Object.entries(exc.row_data).slice(0, 5).map(([k, v]) => `${k}=${fmtValue(v)}`).join("  ")}
                        </div>
                      </td>
                      <td style={{ whiteSpace: "nowrap", color: "var(--text-light)", fontSize: 12 }}>
                        {fmtDateTime(exc.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
