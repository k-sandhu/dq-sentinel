import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type { Dataset } from "../api/types";
import { EmptyState, ErrorBox, Pill, Spinner } from "../components/ui";
import { fmtNum, timeAgo } from "../lib/format";

export default function DatasetsPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Datasets</h1>
          <div className="sub">Tables and views under data-quality monitoring</div>
        </div>
        <Link to="/connections" className="btn">Browse sources</Link>
      </div>
      <ErrorBox error={error} />
      {isLoading ? (
        <Spinner />
      ) : !data?.length ? (
        <div className="card">
          <EmptyState title="No datasets registered" hint="Open a connection and register tables to monitor them.">
            <Link to="/connections" className="btn primary" style={{ background: "var(--brand)", color: "#fff" }}>
              Go to connections
            </Link>
          </EmptyState>
        </div>
      ) : (
        <div className="card table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Health</th>
                <th>Dataset</th>
                <th>Connection</th>
                <th className="num">Rows</th>
                <th className="num">Active checks</th>
                <th className="num">Open exceptions</th>
                <th>Last profiled</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.id} className="clickable" onClick={() => navigate(`/datasets/${d.id}`)}>
                  <td><Pill value={d.health} /></td>
                  <td style={{ fontWeight: 700, color: "var(--text-dark)" }}>
                    {d.schema_name ? `${d.schema_name}.` : ""}
                    {d.table_name}
                  </td>
                  <td style={{ color: "var(--text-light)" }}>{d.connection_name}</td>
                  <td className="num">{fmtNum(d.row_count)}</td>
                  <td className="num">{fmtNum(d.active_checks)}</td>
                  <td className="num" style={{ color: d.open_exceptions ? "var(--danger-dark)" : undefined, fontWeight: d.open_exceptions ? 700 : 400 }}>
                    {fmtNum(d.open_exceptions)}
                  </td>
                  <td style={{ color: "var(--text-light)" }}>{timeAgo(d.last_profiled_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
