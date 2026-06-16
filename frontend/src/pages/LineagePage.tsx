// Estate-wide lineage (issue #51): full view-derived graph for one connection,
// with a "needs attention" rail and a flat edge table underneath.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Connection, LineageGraph as LineageGraphData, LineageNode } from "../api/types";
import LineageGraph from "../components/LineageGraph";
import { EmptyState, ErrorBox, Spinner, StatusPill } from "../components/ui";
import { lineageNodeHref } from "../lib/lineageNav";

function nodeLabel(n: LineageNode): string {
  return n.schema_name ? `${n.schema_name}.${n.table_name}` : n.table_name;
}

export default function LineagePage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [granularity, setGranularity] = useState<"table" | "column">("table");

  const connectionsQuery = useQuery({
    queryKey: ["connections"],
    queryFn: () => api.get<Connection[]>("/connections"),
  });
  const connections = connectionsQuery.data;

  const raw = params.get("connection");
  const fromParam = raw && Number.isFinite(Number(raw)) ? Number(raw) : null;
  const connectionId = fromParam ?? connections?.[0]?.id ?? null;

  // Keep ?connection=ID in sync: write the default into the URL once
  // connections load so the view is shareable/bookmarkable.
  useEffect(() => {
    if (fromParam === null && connections && connections.length > 0) {
      setParams({ connection: String(connections[0].id) }, { replace: true });
    }
  }, [fromParam, connections, setParams]);

  const lineage = useQuery({
    queryKey: ["connection-lineage", connectionId, granularity],
    queryFn: () => api.get<LineageGraphData>(`/connections/${connectionId}/lineage?granularity=${granularity}`),
    enabled: connectionId !== null,
    staleTime: 30_000,
  });
  const graph = lineage.data;

  const byId = new Map<string, LineageNode>();
  for (const n of graph?.nodes ?? []) byId.set(n.id, n);

  const attention = (graph?.nodes ?? [])
    .filter((n) => n.health === "fail" || n.health === "warn")
    .sort((a, b) => {
      if (a.health !== b.health) return a.health === "fail" ? -1 : 1;
      return b.failing_checks - a.failing_checks || b.open_exceptions - a.open_exceptions;
    });

  if (connectionsQuery.isLoading) return <Spinner label="Loading connections…" />;

  return (
    <div className="page wide">
      <div className="page-header">
        <div>
          <h1>Lineage</h1>
          <div className="sub">Upstream/downstream map derived from view definitions, colored by check health</div>
        </div>
        <div className="header-actions">
          {connections && connections.length > 0 && (
            <select
              value={connectionId ?? ""}
              onChange={(e) => setParams({ connection: e.target.value })}
              style={{ marginTop: 0, width: 220 }}
              aria-label="Connection"
            >
              {connections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.kind})
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      <ErrorBox error={connectionsQuery.error} />

      {connections && connections.length === 0 ? (
        <EmptyState
          title="No connections yet"
          hint="Lineage is derived from view definitions in a source database — add a connection first."
        >
          <Link className="btn primary" to="/connections">
            Add a connection
          </Link>
        </EmptyState>
      ) : (
        <>
          <div className="split">
            <div className="card card-pad">
              <div className="section-title" style={{ margin: "0 0 12px" }}>
                <h2>Impact map</h2>
              </div>
              {lineage.isLoading && <Spinner label="Building lineage…" />}
              <ErrorBox error={lineage.error} />
              {graph && (
                <LineageGraph
                  graph={graph}
                  granularity={granularity}
                  onGranularityChange={setGranularity}
                  emptyHint="No tables or views were found on this connection — or none of its views reference other tables."
                />
              )}
            </div>
            <div className="card card-pad">
              <h3>Needs attention</h3>
              {lineage.isLoading && <Spinner />}
              {graph && attention.length === 0 && (
                <EmptyState title="All clear" hint="No failing or warning tables anywhere in this estate." />
              )}
              {attention.length > 0 && (
                <div className="dense-list">
                  {attention.map((n) => {
                    const href = lineageNodeHref(n);
                    return (
                      <div
                        key={n.id}
                        className={`dense-item${href ? " clickable" : ""}`}
                        onClick={() => href && navigate(href)}
                      >
                        <div className="title">
                          {nodeLabel(n)} <StatusPill value={n.health} />
                        </div>
                        <div className="meta">
                          {n.failing_checks} failing check{n.failing_checks === 1 ? "" : "s"} · {n.open_exceptions} open
                          exception{n.open_exceptions === 1 ? "" : "s"}
                        </div>
                        {n.dataset_id !== null && (
                          <div style={{ display: "flex", gap: 12, marginTop: 6, fontSize: 12 }}>
                            <Link to={`/datasets/${n.dataset_id}/exceptions`} onClick={(e) => e.stopPropagation()}>
                              Open exceptions
                            </Link>
                            <Link to={`/datasets/${n.dataset_id}`} onClick={(e) => e.stopPropagation()}>
                              Open profile
                            </Link>
                            <Link to={`/datasets/${n.dataset_id}/lineage`} onClick={(e) => e.stopPropagation()}>
                              Open lineage
                            </Link>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {graph && (
            <>
              <div className="section-title">
                <h2>Relationships ({graph.edges.length})</h2>
              </div>
              <div className="card table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>From (upstream)</th>
                      <th>To (downstream)</th>
                      <th>Relationship</th>
                      <th>Target health</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {graph.edges.length === 0 && (
                      <tr>
                        <td colSpan={5} style={{ color: "var(--text-light)" }}>
                          No view-derived relationships on this connection.
                        </td>
                      </tr>
                    )}
                    {graph.edges.map((e, i) => {
                      const target = byId.get(e.target);
                      return (
                        <tr key={`${e.source}->${e.target}-${i}`}>
                          <td className="mono">{e.source}</td>
                          <td className="mono">{e.target}</td>
                          <td>feeds</td>
                          <td>
                            <StatusPill value={target?.health ?? "unknown"} />
                          </td>
                          <td style={{ whiteSpace: "nowrap" }}>
                            {target?.dataset_id != null && (
                              <>
                                <Link to={`/datasets/${target.dataset_id}`} style={{ marginRight: 12 }}>
                                  Open profile
                                </Link>
                                <Link to={`/datasets/${target.dataset_id}/lineage`}>Open lineage</Link>
                              </>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
