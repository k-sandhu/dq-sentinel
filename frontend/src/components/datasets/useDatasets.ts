import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Dataset } from "../../api/types";

/** The shared datasets list (FE-4). Same key + options as before — just routed
 *  through the query-key factory so the table, the sidebar favorites, and the
 *  recents palette all share one cache entry. */
export function useDatasets() {
  return useQuery({
    queryKey: qk.datasets.list(),
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });
}
