import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Dataset } from "../api/types";
import Breadcrumbs from "../components/Breadcrumbs";
import ExceptionsTriage from "../components/ExceptionsTriage";

export default function ExceptionsPage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run_id") ? Number(params.get("run_id")) : undefined;
  const datasetFilter = params.get("dataset_id") ? Number(params.get("dataset_id")) : undefined;

  const { data: datasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });

  const selectedDataset = datasets?.find((dataset) => dataset.id === datasetFilter);
  const selectedDatasetLabel =
    selectedDataset?.display_name || selectedDataset?.table_name || `#${datasetFilter}`;
  const breadcrumbItems = datasetFilter
    ? [
        { label: "Datasets", to: "/datasets" },
        { label: selectedDatasetLabel, to: `/datasets/${datasetFilter}` },
        { label: "Exceptions" },
      ]
    : runId
      ? [{ label: "Runs", to: "/runs" }, { label: `Run #${runId}`, to: `/runs/${runId}` }, { label: "Exceptions" }]
      : [{ label: "Exceptions" }];

  const setFilterParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  return (
    <div className="page">
      <Breadcrumbs items={breadcrumbItems} />
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
              setFilterParam("dataset_id", e.target.value || null);
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
      {(datasetFilter || runId) && (
        <div className="chip-row" style={{ marginBottom: 14 }}>
          {datasetFilter && (
            <button
              type="button"
              className="filter-chip on"
              onClick={() => setFilterParam("dataset_id", null)}
              title="Remove dataset filter"
            >
              Dataset: {selectedDatasetLabel} <span aria-hidden="true">x</span>
            </button>
          )}
          {runId && (
            <button
              type="button"
              className="filter-chip on"
              onClick={() => setFilterParam("run_id", null)}
              title="Remove run filter"
            >
              Run: #{runId} <span aria-hidden="true">x</span>
            </button>
          )}
        </div>
      )}
      <ExceptionsTriage datasetId={datasetFilter} runId={runId} />
    </div>
  );
}
