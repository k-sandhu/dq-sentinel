import { Link, useNavigate } from "react-router";
import type { Run } from "../api/types";
import { checkTypeLabel } from "../lib/checkMeta";
import { fmtDateTime, fmtNum } from "../lib/format";
import { EmptyState, StatusPill } from "./ui";

export default function RunsTable({
  runs,
  showDataset = true,
}: {
  runs: Run[];
  showDataset?: boolean;
}) {
  const navigate = useNavigate();
  if (!runs.length) return <EmptyState title="No runs yet" hint="Activate a check and run it, or wait for the scheduler." />;
  return (
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
          {runs.map((r) => (
            <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
              <td>
                <StatusPill value={r.status} />
              </td>
              <td>
                <div style={{ fontWeight: 600 }}>
                  <Link to={`/runs/${r.id}`} className="row-title-link" onClick={(e) => e.stopPropagation()}>
                    {r.check_name}
                  </Link>
                </div>
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
              <td style={{ whiteSpace: "nowrap" }}>
                {r.exception_count > 0 && (
                  <Link to={`/exceptions?run_id=${r.id}`} onClick={(e) => e.stopPropagation()}>
                    {r.exception_count} exception{r.exception_count === 1 ? "" : "s"}
                  </Link>
                )}
                {(r.status === "fail" || r.status === "error" || r.status === "warn") && (
                  <Link
                    to={`/workbench?dataset_id=${r.dataset_id}&run_id=${r.id}`}
                    title="Open the workbench with suggested investigation queries for this failure"
                    onClick={(e) => e.stopPropagation()}
                    style={{ marginLeft: r.exception_count > 0 ? 8 : 0 }}
                  >
                    Investigate in workbench →
                  </Link>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
