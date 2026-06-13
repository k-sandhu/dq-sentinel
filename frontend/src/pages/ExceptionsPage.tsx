import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Dataset } from "../api/types";
import ExceptionsTriage from "../components/ExceptionsTriage";
import { Breadcrumbs } from "../components/ui";

export default function ExceptionsPage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run_id") ? Number(params.get("run_id")) : undefined;
  const datasetFilter = params.get("dataset_id") ? Number(params.get("dataset_id")) : undefined;

  const { data: datasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });
  const datasetName = datasets?.find((d) => d.id === datasetFilter)?.table_name;

  // Mutate a clone of the current params so other filters (e.g. run_id) survive (BF-2).
  const patchParams = (mutate: (p: URLSearchParams) => void) => {
    const next = new URLSearchParams(params);
    mutate(next);
    setParams(next);
  };

  return (
    <div className="page">
      {datasetFilter && (
        <Breadcrumbs
          items={[
            { label: "Datasets", to: "/datasets" },
            ...(datasetName ? [{ label: datasetName, to: `/datasets/${datasetFilter}` }] : []),
            { label: "Exceptions" },
          ]}
        />
      )}
      <div className="page-header">
        <div>
          <h1>Exceptions</h1>
          <div className="sub">
            Violating rows captured by failed checks — triage them to build institutional memory
          </div>
        </div>
        <div className="header-actions">
          <select
            value={datasetFilter ?? ""}
            onChange={(e) =>
              patchParams((p) => {
                if (e.target.value) p.set("dataset_id", e.target.value);
                else p.delete("dataset_id");
              })
            }
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
      {runId != null && (
        <div className="active-filters">
          <span className="filter-tag">
            run #{runId}
            <button
              type="button"
              className="tag-x"
              aria-label="Clear run filter"
              onClick={() => patchParams((p) => p.delete("run_id"))}
            >
              ×
            </button>
          </span>
        </div>
      )}
      <ExceptionsTriage datasetId={datasetFilter} runId={runId} />
    </div>
  );
}
