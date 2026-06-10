import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Dataset } from "../api/types";
import ExceptionsTriage from "../components/ExceptionsTriage";

export default function ExceptionsPage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run_id") ? Number(params.get("run_id")) : undefined;
  const datasetFilter = params.get("dataset_id") ? Number(params.get("dataset_id")) : undefined;

  const { data: datasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Exceptions</h1>
          <div className="sub">
            Violating rows captured by failed checks — triage them to build institutional memory
            {runId ? ` (filtered to run #${runId})` : ""}
          </div>
        </div>
        <div className="header-actions">
          <select
            value={datasetFilter ?? ""}
            onChange={(e) => {
              const next = new URLSearchParams();
              if (e.target.value) next.set("dataset_id", e.target.value);
              setParams(next);
            }}
            style={{ marginTop: 0, width: 220 }}
          >
            <option value="">All datasets</option>
            {datasets?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.table_name}
              </option>
            ))}
          </select>
        </div>
      </div>
      <ExceptionsTriage datasetId={datasetFilter} runId={runId} />
    </div>
  );
}
