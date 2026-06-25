// Estate-wide lineage (issue #51): the full view-derived graph for one connection.
// The interactive explorer (search, focus, health filter, side panel with a
// "needs attention" jump list) is the whole page now — the old split layout,
// standalone attention card, and flat edge table were redundant with it.
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import LineageGraph from "../components/LineageGraph";
import { resolveLineageConnection } from "../components/lineage/lineageSelection";
import { useConnectionLineage, useConnections } from "../components/lineage/useConnectionLineage";
import { EmptyState, ErrorBox, Spinner } from "../components/ui";

export default function LineagePage() {
  const [params, setParams] = useSearchParams();
  const [granularity, setGranularity] = useState<"table" | "column">("table");

  const connectionsQuery = useConnections();
  const connections = connectionsQuery.data;

  const { fromParam, connectionId } = resolveLineageConnection(params.get("connection"), connections);

  // Keep ?connection=ID in sync: write the default into the URL once
  // connections load so the view is shareable/bookmarkable.
  useEffect(() => {
    if (fromParam === null && connections && connections.length > 0) {
      setParams({ connection: String(connections[0].id) }, { replace: true });
    }
  }, [fromParam, connections, setParams]);

  const lineage = useConnectionLineage(connectionId, granularity);
  const graph = lineage.data;

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
        </>
      )}
    </div>
  );
}
