import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../api/client";
import type { Connection, ConnectionHealth, ConnectionTest, Dataset } from "../api/types";
import { isAdmin, useAuth } from "../auth";
import Breadcrumbs from "../components/Breadcrumbs";
import { EmptyState, ErrorBox, Icon, Pill, Spinner, StatCard } from "../components/ui";
import { fmtDateTime, fmtNum } from "../lib/format";

interface HealthSnapshot extends ConnectionTest {
  checked_at: string;
}

export default function ConnectionDetailPage() {
  const { id } = useParams();
  const connectionId = Number(id);
  const { user } = useAuth();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [history, setHistory] = useState<HealthSnapshot[]>([]);

  const connectionsQuery = useQuery({
    queryKey: ["connections"],
    queryFn: () => api.get<Connection[]>("/connections"),
  });
  const datasetsQuery = useQuery({
    queryKey: ["datasets", { connectionId }],
    queryFn: () => api.get<Dataset[]>(`/datasets?connection_id=${connectionId}`),
  });
  const fleetHealth = useQuery({
    queryKey: ["fleet-health"],
    queryFn: () => api.get<ConnectionHealth[]>("/connections/health"),
    enabled: false,
  });

  const connection = connectionsQuery.data?.find((c) => c.id === connectionId);
  const currentHealth = fleetHealth.data?.find((h) => h.id === connectionId);

  useEffect(() => {
    if (connection) setName(connection.name);
  }, [connection?.id, connection?.name]);

  const rename = useMutation({
    mutationFn: () => api.patch<Connection>(`/connections/${connectionId}`, { name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connections"] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
  const test = useMutation({
    mutationFn: () => api.post<ConnectionTest>(`/connections/${connectionId}/test`),
    onSuccess: (result) => {
      setHistory((prev) => [{ ...result, checked_at: new Date().toISOString() }, ...prev].slice(0, 8));
      qc.invalidateQueries({ queryKey: ["fleet-health"] });
    },
  });

  if (connectionsQuery.isLoading) return <Spinner label="Loading connection..." />;

  return (
    <div className="page">
      <Breadcrumbs items={[{ label: "Connections", to: "/connections" }, { label: connection?.name ?? `Connection #${connectionId}` }]} />
      <ErrorBox error={connectionsQuery.error || datasetsQuery.error || fleetHealth.error || rename.error || test.error} />
      {!connection ? (
        <div className="card">
          <EmptyState title="Connection not found" />
        </div>
      ) : (
        <>
          <div className="page-header">
            <div>
              <h1>{connection.name}</h1>
              <div className="sub">
                <span className="badge kind">{connection.kind}</span> · added {fmtDateTime(connection.created_at)}
              </div>
            </div>
            <div className="header-actions">
              <Link to={`/connections/${connection.id}/browse`} className="btn">
                <Icon name="search" size={13} /> Browse tables
              </Link>
              <button onClick={() => test.mutate()} disabled={test.isPending}>
                <Icon name="bolt" size={13} /> {test.isPending ? "Testing..." : "Test connection"}
              </button>
            </div>
          </div>

          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <StatCard label="Datasets" value={fmtNum(connection.dataset_count)} />
            <StatCard label="Engine" value={<span className="badge kind">{connection.kind}</span>} />
            <StatCard
              label="Fleet status"
              value={currentHealth ? <Pill value={currentHealth.ok ? "pass" : "fail"} /> : "Not probed"}
            />
            <StatCard label="DSN" value={<span className="mono">{connection.dsn_masked}</span>} />
          </div>

          <div className="grid cols-2">
            {isAdmin(user) && (
              <div className="card card-pad">
                <h3>Rename</h3>
                <div className="form-row">
                  <label className="field">
                    Display name
                    <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
                  </label>
                  <div style={{ alignSelf: "end" }}>
                    <button
                      className="primary"
                      onClick={() => rename.mutate()}
                      disabled={!name.trim() || name === connection.name || rename.isPending}
                    >
                      Save name
                    </button>
                  </div>
                </div>
              </div>
            )}
            <div className="card card-pad">
              <h3>Health Checks</h3>
              {!history.length ? (
                <div className="field-hint">Run a connection test to build a page-local health trail.</div>
              ) : (
                <div className="timeline">
                  {history.map((item) => (
                    <div key={item.checked_at} className={`timeline-item ${item.ok ? "" : "fail"}`}>
                      <span className="timeline-dot" />
                      <div className="title">{item.ok ? "Reachable" : "Failed"}</div>
                      <div className="meta">
                        {fmtDateTime(item.checked_at)} · {item.message}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="section-title">
            <h2>Datasets</h2>
            <Link to={`/connections/${connection.id}/browse`}>Register more →</Link>
          </div>
          {!datasetsQuery.data?.length ? (
            <div className="card">
              <EmptyState title="No datasets registered from this connection" />
            </div>
          ) : (
            <div className="card table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Dataset</th>
                    <th>Health</th>
                    <th className="num">Rows</th>
                    <th className="num">Checks</th>
                    <th className="num">Open exceptions</th>
                    <th>Profiled</th>
                  </tr>
                </thead>
                <tbody>
                  {datasetsQuery.data.map((dataset) => (
                    <tr key={dataset.id}>
                      <td>
                        <Link to={`/datasets/${dataset.id}`}>
                          {dataset.schema_name ? `${dataset.schema_name}.` : ""}
                          {dataset.table_name}
                        </Link>
                      </td>
                      <td><Pill value={dataset.health} /></td>
                      <td className="num">{fmtNum(dataset.row_count)}</td>
                      <td className="num">{fmtNum(dataset.active_checks)}</td>
                      <td className="num">{fmtNum(dataset.open_exceptions)}</td>
                      <td style={{ color: "var(--text-light)" }}>{fmtDateTime(dataset.last_profiled_at)}</td>
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
