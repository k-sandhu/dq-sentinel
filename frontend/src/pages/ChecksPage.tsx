import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Check } from "../api/types";
import ChecksTable from "../components/ChecksTable";
import { EmptyState, ErrorBox, Spinner } from "../components/ui";

const FILTERS = ["all", "active", "proposed", "disabled"] as const;
type CheckFilter = (typeof FILTERS)[number];

function asCheckFilter(value: string | null): CheckFilter {
  return FILTERS.includes(value as CheckFilter) ? (value as CheckFilter) : "all";
}

export default function ChecksPage() {
  const [params, setParams] = useSearchParams();
  const filter = asCheckFilter(params.get("status"));
  const lastStatuses = params.getAll("last_status").filter(Boolean);
  const search = params.get("q") ?? "";

  const patchParams = (mutate: (p: URLSearchParams) => void, replace = false) => {
    const next = new URLSearchParams(params);
    mutate(next);
    setParams(next, { replace });
  };
  const setFilter = (nextFilter: CheckFilter) =>
    patchParams((p) => {
      if (nextFilter === "all") p.delete("status");
      else p.set("status", nextFilter);
    });
  const setSearch = (value: string) =>
    patchParams((p) => {
      if (value) p.set("q", value);
      else p.delete("q");
    }, true);
  const clearLastStatuses = () => patchParams((p) => p.delete("last_status"));

  const { data, isLoading, error } = useQuery({
    queryKey: ["checks", { filter }],
    queryFn: () => api.get<Check[]>(`/checks${filter === "all" ? "" : `?status=${filter}`}`),
  });

  const needle = search.toLowerCase();
  const shown = (data ?? []).filter(
    (c) =>
      (lastStatuses.length === 0 || lastStatuses.includes(c.last_status ?? "unknown")) &&
      (!needle ||
        c.name.toLowerCase().includes(needle) ||
        (c.column_name ?? "").toLowerCase().includes(needle) ||
        c.dataset_name.toLowerCase().includes(needle) ||
        c.check_type.includes(needle)),
  );

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Checks</h1>
          <div className="sub">
            Every rule guarding your data, across all datasets
            {data ? ` · ${shown.length} of ${data.length} shown` : ""}
          </div>
        </div>
      </div>
      <div className="toolbar">
        <input
          type="text"
          aria-label="Search checks"
          placeholder="Search checks by name, column, type or dataset…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: 320, marginTop: 0 }}
        />
        <div className="chip-row">
          {FILTERS.map((f) => (
            <button key={f} className={`filter-chip${filter === f ? " on" : ""}`} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
      </div>
      {lastStatuses.length > 0 && (
        <div className="active-filters">
          <span className="filter-tag">
            latest result {lastStatuses.join(" / ")}
            <button
              type="button"
              className="tag-x"
              aria-label="Clear latest result filter"
              onClick={clearLastStatuses}
            >
              &times;
            </button>
          </span>
        </div>
      )}
      <ErrorBox error={error} />
      {isLoading ? (
        <Spinner />
      ) : !shown.length ? (
        <div className="card">
          <EmptyState
            title={filter !== "all" || search ? "No checks match your filters" : "No checks yet"}
            hint={
              filter !== "all" || search
                ? "Clear the search or switch the status chip to see more."
                : "Profile a dataset, then generate checks — or add one manually from its Checks tab."
            }
          />
        </div>
      ) : (
        <ChecksTable checks={shown} />
      )}
    </div>
  );
}
