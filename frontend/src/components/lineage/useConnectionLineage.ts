import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Connection, LineageGraph as LineageGraphData } from "../../api/types";

/** All connections — feeds the lineage connection picker and the default selection. */
export function useConnections() {
  return useQuery({
    queryKey: qk.connections.list(),
    queryFn: () => api.get<Connection[]>("/connections"),
  });
}

/** View-derived lineage graph for one connection at a granularity (FE-4). Same key
 *  + options as before: gated on a selected connection, 30s stale window. */
export function useConnectionLineage(
  connectionId: number | null,
  granularity: "table" | "column",
) {
  return useQuery({
    queryKey: qk.connectionLineage.detail(connectionId, granularity),
    queryFn: () =>
      api.get<LineageGraphData>(`/connections/${connectionId}/lineage?granularity=${granularity}`),
    enabled: connectionId !== null,
    staleTime: 30_000,
  });
}
