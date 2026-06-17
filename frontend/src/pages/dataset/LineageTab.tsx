// Dataset "Lineage" tab (issue #51): BFS neighborhood around this dataset's
// node. The depth selector lives in the graph's own toolbar (no duplicate here);
// this slim header just links out to the estate-wide map.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import type { Dataset, LineageGraph as LineageGraphData } from "../../api/types";
import LineageGraph from "../../components/LineageGraph";
import { ErrorBox, Icon, Spinner } from "../../components/ui";

export default function LineageTab({ dataset }: { dataset: Dataset }) {
  const [depth, setDepth] = useState(2);
  const [granularity, setGranularity] = useState<"table" | "column">("table");
  const { data, isLoading, error } = useQuery({
    queryKey: ["lineage-dataset", dataset.id, depth, granularity],
    queryFn: () => api.get<LineageGraphData>(`/datasets/${dataset.id}/lineage?depth=${depth}&granularity=${granularity}`),
    staleTime: 30_000,
    placeholderData: (prev) => prev, // keep the old graph while a new depth loads
  });

  // Matches the backend's node_id_for(): lowercased "schema.table" or "table".
  const currentId = (
    dataset.schema_name ? `${dataset.schema_name}.${dataset.table_name}` : dataset.table_name
  ).toLowerCase();

  return (
    <div className="card card-pad">
      <div className="toolbar">
        <span style={{ fontSize: 12.5, color: "var(--text-light)" }}>
          Neighborhood around{" "}
          <strong style={{ color: "var(--text-dark)" }}>{dataset.table_name}</strong>
        </span>
        <div className="right">
          <Link className="btn small" to={`/lineage?connection=${dataset.connection_id}`}>
            <Icon name="graph" size={12} /> Open estate lineage
          </Link>
        </div>
      </div>
      <ErrorBox error={error} />
      {isLoading && <Spinner label="Tracing lineage…" />}
      {data && (
        <LineageGraph
          graph={data}
          currentId={currentId}
          granularity={granularity}
          onGranularityChange={setGranularity}
          depth={depth}
          onDepthChange={setDepth}
          emptyHint="No parsed view definitions feed or read from this dataset."
        />
      )}
    </div>
  );
}
