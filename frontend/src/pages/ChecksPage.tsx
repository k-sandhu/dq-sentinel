import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Check } from "../api/types";
import ChecksTable from "../components/ChecksTable";
import { CheckViewSwitch, useCheckView } from "../components/CheckViews";
import { EmptyState, ErrorBox, Spinner } from "../components/ui";

const FILTERS = ["all", "active", "disabled"] as const;
type CheckFilter = (typeof FILTERS)[number];

function asCheckFilter(value: string | null): CheckFilter {
  return FILTERS.includes(value as CheckFilter) ? (value as CheckFilter) : "all";
}

export default function ChecksPage() {
  const [params, setParams] = useSearchParams();
  const { view, setView } = useCheckView();
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
    queryKey: ["checks", "workspace"],
    queryFn: () => api.get<Check[]>("/checks"),
  });

  const proposals = (data ?? []).filter((c) => c.status === "proposed");
  const inView = (data ?? []).filter((c) => view === "proposals" ? c.status === "proposed" : c.status !== "proposed" && c.status !== "archived");
  const needle = search.toLowerCase();
  const shown = inView.filter(
    (c) =>
      (view === "proposals" || ((filter === "all" || c.status === filter) && (lastStatuses.length === 0 || lastStatuses.includes(c.last_status ?? "unknown")))) &&
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
            {data ? ` · ${shown.length} of ${inView.length} ${view} shown` : ""}
          </div>
        </div>
      </div>
      <CheckViewSwitch view={view} proposals={proposals.length} onChange={setView} />
      <p className="check-scope">{view === "results"
        ? "Latest recorded results, with run errors, failures and warnings first. Includes paused checks and checks that have never run. Archived checks are excluded."
        : "Proposals do not run until you activate them. Review their rationale and parameters before activating. Bulk actions apply to shown proposals."}</p>
      <div className="toolbar">
        <input
          type="text"
          aria-label="Search checks"
          placeholder="Search checks by name, column, type or dataset…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: 320, marginTop: 0 }}
        />
        {view === "results" && <div className="chip-row" role="group" aria-label="Check status">
          {FILTERS.map((f) => (
            <button key={f} aria-pressed={filter === f} className={`filter-chip${filter === f ? " on" : ""}`} onClick={() => setFilter(f)}>
              {f === "all" ? "All current checks" : f === "disabled" ? "Paused" : "Active"}
            </button>
          ))}
        </div>}
      </div>
      {view === "results" && lastStatuses.length > 0 && (
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
            title={search || (view === "results" && (filter !== "all" || lastStatuses.length > 0)) ? "No checks match your filters" : view === "proposals" ? "No proposals to review" : "No operational checks yet"}
            hint={
              search || (view === "results" && (filter !== "all" || lastStatuses.length > 0))
                ? "Clear the search or switch the status chip to see more."
                : view === "proposals" ? "Generate proposals from a profiled dataset." : "Review and activate proposals, or add a check from a dataset's Checks tab."
            }
          />
        </div>
      ) : (
        <ChecksTable checks={shown} />
      )}
    </div>
  );
}
