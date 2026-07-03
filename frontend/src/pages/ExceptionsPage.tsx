import { useSearchParams } from "react-router";
import { useDatasets } from "../components/datasets/useDatasets";
import ExceptionsTriage from "../components/ExceptionsTriage";
import { Breadcrumbs } from "../components/ui";

export default function ExceptionsPage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run_id") ? Number(params.get("run_id")) : undefined;
  const datasetFilter = params.get("dataset_id") ? Number(params.get("dataset_id")) : undefined;
  // check_id pins the workspace to one check (e.g. the "My work" failing-now
  // deep-link). Other URL filters (status/assignee/recurrence) are read by the
  // workspace itself; these three pinned ids are passed as props.
  const checkId = params.get("check_id") ? Number(params.get("check_id")) : undefined;

  const { data: datasets } = useDatasets();
  const datasetName = datasets?.find((d) => d.id === datasetFilter)?.table_name;

  // Mutate a clone of the current params so other filters (e.g. run_id) survive (BF-2).
  const patchParams = (mutate: (p: URLSearchParams) => void) => {
    const next = new URLSearchParams(params);
    mutate(next);
    setParams(next);
  };

  return (
    <div className="page wide">
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
            aria-label="Filter by dataset"
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
                {(d.schema_name ? `${d.schema_name}.${d.table_name}` : d.table_name) +
                  ` · ${d.connection_name}`}
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
      <ExceptionsTriage datasetId={datasetFilter} runId={runId} checkId={checkId} />
    </div>
  );
}
