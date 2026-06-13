import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type { Health, RcaSession, Run } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { checkTypeLabel } from "../lib/checkMeta";
import { fmtDateTime, fmtNum } from "../lib/format";
import { EmptyState, ErrorBox, Icon, Pill } from "./ui";

export default function RunsTable({
  runs,
  showDataset = true,
}: {
  runs: Run[];
  showDataset?: boolean;
}) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const startRca = useMutation({
    mutationFn: (run: Run) => api.post<RcaSession>("/rca/start", { check_run_id: run.id }),
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: ["rca"] });
      navigate(`/datasets/${session.dataset_id}/rca`);
    },
  });
  const canStartRca = canEdit(user) && Boolean(health?.llm_enabled);

  if (!runs.length) return <EmptyState title="No runs yet" hint="Activate a check and run it, or wait for the scheduler." />;
  return (
    <>
      <ErrorBox error={startRca.error} />
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Status</th>
              <th>Check</th>
              {showDataset && <th>Dataset</th>}
              <th className="num">Violations</th>
              <th className="num">Rows</th>
              <th>Detail</th>
              <th>Trigger</th>
              <th>Started</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const canInvestigate = run.status === "fail" || run.status === "error" || run.status === "warn";
              return (
                <tr key={run.id} className="clickable" onClick={() => navigate(`/runs/${run.id}`)}>
                  <td>
                    <Pill value={run.status} />
                  </td>
                  <td>
                    <Link to={`/checks/${run.check_id}`} onClick={(e) => e.stopPropagation()} style={{ fontWeight: 600 }}>
                      {run.check_name}
                    </Link>
                    <div style={{ fontSize: 11.5, color: "var(--text-light)" }}>{checkTypeLabel(run.check_type)}</div>
                  </td>
                  {showDataset && (
                    <td>
                      <Link to={`/datasets/${run.dataset_id}`} onClick={(e) => e.stopPropagation()}>
                        {run.dataset_name}
                      </Link>
                    </td>
                  )}
                  <td className="num" style={{ fontWeight: 700, color: run.violation_count ? "var(--danger-dark)" : undefined }}>
                    {fmtNum(run.violation_count)}
                  </td>
                  <td className="num">{fmtNum(run.rows_evaluated)}</td>
                  <td style={{ maxWidth: 260, fontSize: 12 }}>
                    {run.error_message ? (
                      <span style={{ color: "var(--danger-dark)" }}>{run.error_message.slice(0, 120)}</span>
                    ) : (
                      String(run.metrics?.detail ?? "")
                    )}
                  </td>
                  <td>
                    <span className="badge">{run.triggered_by}</span>
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>{fmtDateTime(run.started_at)}</td>
                  <td style={{ whiteSpace: "nowrap" }} onClick={(e) => e.stopPropagation()}>
                    <Link to={`/runs/${run.id}`}>View</Link>
                    {run.exception_count > 0 && (
                      <Link to={`/exceptions?run_id=${run.id}`} style={{ marginLeft: 8 }}>
                        {run.exception_count} exception{run.exception_count === 1 ? "" : "s"}
                      </Link>
                    )}
                    {canInvestigate && (
                      <Link
                        to={`/workbench?dataset_id=${run.dataset_id}&run_id=${run.id}`}
                        title="Open the workbench with suggested investigation queries for this failure"
                        style={{ marginLeft: 8 }}
                      >
                        investigate -&gt;
                      </Link>
                    )}
                    {canInvestigate && canStartRca && (
                      <button
                        className="small"
                        onClick={() => startRca.mutate(run)}
                        disabled={startRca.isPending}
                        style={{ marginLeft: 8 }}
                      >
                        <Icon name="bolt" size={12} /> RCA
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
