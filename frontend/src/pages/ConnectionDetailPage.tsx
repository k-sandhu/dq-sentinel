import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import type { Connection, ConnectionTest, Dataset } from "../api/types";
import { EmptyState, ErrorBox, Icon, NotFoundState, Spinner, StatCard, StatusPill } from "../components/ui";
import { fmtDateTime, fmtNum, timeAgo } from "../lib/format";

type HealthEvent = ConnectionTest & { checked_at: string };

export default function ConnectionDetailPage() {
  const { id } = useParams();
  const connectionId = Number(id);
  const navigate = useNavigate();
  const [history, setHistory] = useState<HealthEvent[]>([]);

  const connectionsQuery = useQuery({
    queryKey: ["connections"],
    queryFn: () => api.get<Connection[]>("/connections"),
  });
  const datasetsQuery = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });

  const connection = connectionsQuery.data?.find((c) => c.id === connectionId) ?? null;
  const datasets = (datasetsQuery.data ?? []).filter((d) => d.connection_id === connectionId);

  const testConnection = useMutation({
    mutationFn: () => api.post<ConnectionTest>(`/connections/${connectionId}/test`),
    onSuccess: (result) => {
      setHistory((prev) => [{ ...result, checked_at: new Date().toISOString() }, ...prev].slice(0, 8));
    },
  });

  if (connectionsQuery.isLoading) return <Spinner label="Loading connection..." />;
  if (connectionsQuery.error) return <div className="page"><ErrorBox error={connectionsQuery.error} /></div>;
  if (!connection) {
    return <NotFoundState what="Connection" backTo="/connections" backLabel="Back to connections" />;
  }

  const latest = history[0];
  const openExceptions = datasets.reduce((sum, d) => sum + d.open_exceptions, 0);
  const activeChecks = datasets.reduce((sum, d) => sum + d.active_checks, 0);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>{connection.name}</h1>
          <div className="sub">
            <Link to="/connections">Connections</Link> · <span className="badge kind">{connection.kind}</span> · added{" "}
            {fmtDateTime(connection.created_at)}
          </div>
        </div>
        <div className="header-actions">
          <button onClick={() => testConnection.mutate()} disabled={testConnection.isPending}>
            {testConnection.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="bolt" size={13} />}
            {testConnection.isPending ? "Testing..." : "Test connection"}
          </button>
          <Link to={`/connections/${connection.id}/browse`} className="btn primary">
            <Icon name="search" size={13} /> Browse tables
          </Link>
        </div>
      </div>

      <ErrorBox error={testConnection.error || datasetsQuery.error} />

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <StatCard label="Datasets" value={fmtNum(connection.dataset_count)} />
        <StatCard label="Active checks" value={fmtNum(activeChecks)} />
        <StatCard label="Open exceptions" value={fmtNum(openExceptions)} tone={openExceptions ? "danger" : "ok"} />
        <StatCard
          label="Latest health"
          value={latest ? <StatusPill value={latest.ok ? "pass" : "fail"} /> : <StatusPill value="unknown" />}
          hint={latest ? timeAgo(latest.checked_at) : "not checked on this page"}
        />
      </div>

      <div className="split" style={{ marginBottom: 16 }}>
        <div className="card card-pad">
          <h3>Connection details</h3>
          <div className="table-wrap">
            <table className="data">
              <tbody>
                <tr>
                  <td style={{ fontWeight: 700, width: 140 }}>Engine</td>
                  <td><span className="badge kind">{connection.kind}</span></td>
                </tr>
                <tr>
                  <td style={{ fontWeight: 700 }}>DSN</td>
                  <td className="mono" style={{ overflowWrap: "anywhere" }}>{connection.dsn_masked}</td>
                </tr>
                <tr>
                  <td style={{ fontWeight: 700 }}>Created</td>
                  <td>{fmtDateTime(connection.created_at)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div className="card card-pad">
          <h3>Health checks</h3>
          {!history.length ? (
            <EmptyState title="No health checks yet" hint="Run a test to verify the source is reachable." />
          ) : (
            <div className="timeline">
              {history.map((h) => (
                <div key={h.checked_at} className={`timeline-item ${h.ok ? "" : "fail"}`}>
                  <span className="timeline-dot" />
                  <div className="title">{h.ok ? "Reachable" : "Unreachable"}</div>
                  <div className="meta">
                    {fmtDateTime(h.checked_at)} · {h.message}
                    {h.table_count != null ? ` · ${fmtNum(h.table_count)} tables` : ""}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="section-title">
        <h2>Datasets</h2>
        <Link to={`/connections/${connection.id}/browse`} className="btn small">
          Browse tables
        </Link>
      </div>
      {datasetsQuery.isLoading ? (
        <Spinner />
      ) : !datasets.length ? (
        <div className="card">
          <EmptyState title="No datasets registered" hint="Browse this source and register tables to monitor them." />
        </div>
      ) : (
        <div className="card table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Health</th>
                <th>Dataset</th>
                <th className="num">Rows</th>
                <th className="num">Active checks</th>
                <th className="num">Open exceptions</th>
                <th>Last profiled</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map((d) => (
                <tr key={d.id} className="clickable" onClick={() => navigate(`/datasets/${d.id}`)}>
                  <td><StatusPill value={d.health} /></td>
                  <td style={{ fontWeight: 700 }}>
                    <Link to={`/datasets/${d.id}`} className="row-title-link" onClick={(e) => e.stopPropagation()}>
                      {d.schema_name ? `${d.schema_name}.` : ""}
                      {d.table_name}
                    </Link>
                  </td>
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
