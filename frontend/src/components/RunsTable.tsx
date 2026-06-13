import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type { Health, RcaSession, Run } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { checkTypeLabel } from "../lib/checkMeta";
import { fmtDateTime, fmtNum } from "../lib/format";
import { EmptyState, ErrorBox, Icon, StatusPill } from "./ui";

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
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const startRca = useMutation({
    mutationFn: (run: Run) => api.post<RcaSession>("/rca/start", { check_run_id: run.id }),
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: ["rca"] });
      navigate(`/datasets/${session.dataset_id}/rca`);
    },
  });

  const canStartRca = (run: Run) =>
    canEdit(user) && health.data?.llm_enabled && ["fail", "warn", "error"].includes(run.status);

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
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
                <td>
                  <StatusPill value={r.status} />
                </td>
                <td>
                  <Link
                    to={`/checks/${r.check_id}`}
                    onClick={(e) => e.stopPropagation()}
                    style={{ fontWeight: 600, color: "var(--text-dark)" }}
                  >
                    {r.check_name}
                  </Link>
                  <div style={{ fontSize: 11.5, color: "var(--text-light)" }}>{checkTypeLabel(r.check_type)}</div>
                </td>
                {showDataset && (
                  <td>
                    <Link to={`/datasets/${r.dataset_id}`} onClick={(e) => e.stopPropagation()}>
                      {r.dataset_name}
                    </Link>
                  </td>
                )}
                <td className="num" style={{ fontWeight: 700, color: r.violation_count ? "var(--danger-dark)" : undefined }}>
                  {fmtNum(r.violation_count)}
                </td>
                <td className="num">{fmtNum(r.rows_evaluated)}</td>
                <td style={{ maxWidth: 260, fontSize: 12 }}>
                  {r.error_message
                    ? <span style={{ color: "var(--danger-dark)" }}>{r.error_message.slice(0, 120)}</span>
                    : String(r.metrics?.detail ?? "")}
                </td>
                <td>
                  <span className="badge">{r.triggered_by}</span>
                </td>
                <td style={{ whiteSpace: "nowrap" }}>{fmtDateTime(r.started_at)}</td>
                <td style={{ whiteSpace: "nowrap" }} onClick={(e) => e.stopPropagation()}>
                  {r.exception_count > 0 && (
                    <Link to={`/exceptions?run_id=${r.id}`}>
                      {r.exception_count} exception{r.exception_count === 1 ? "" : "s"}
                    </Link>
                  )}
                  {(r.status === "fail" || r.status === "error" || r.status === "warn") && (
                    <>
                      <Link
                        to={`/workbench?dataset_id=${r.dataset_id}&run_id=${r.id}`}
                        title="Open the workbench with suggested SQL for this run"
                        style={{ marginLeft: r.exception_count > 0 ? 8 : 0 }}
                      >
                        Workbench
                      </Link>
                      {canStartRca(r) && (
                        <button
                          className="small"
                          onClick={() => startRca.mutate(r)}
                          disabled={startRca.isPending}
                          title="Start root-cause analysis for this run"
                          style={{ marginLeft: 8 }}
                        >
                          <Icon name="bolt" size={12} />
                          RCA
                        </button>
                      )}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
