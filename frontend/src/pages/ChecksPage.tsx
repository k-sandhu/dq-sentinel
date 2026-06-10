import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Check } from "../api/types";
import ChecksTable from "../components/ChecksTable";
import { ErrorBox, Spinner } from "../components/ui";

const FILTERS = ["all", "active", "proposed", "disabled"] as const;

export default function ChecksPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [search, setSearch] = useState("");
  const { data, isLoading, error } = useQuery({
    queryKey: ["checks", { filter }],
    queryFn: () => api.get<Check[]>(`/checks${filter === "all" ? "" : `?status=${filter}`}`),
  });

  const needle = search.toLowerCase();
  const shown = (data ?? []).filter(
    (c) =>
      !needle ||
      c.name.toLowerCase().includes(needle) ||
      (c.column_name ?? "").toLowerCase().includes(needle) ||
      c.dataset_name.toLowerCase().includes(needle) ||
      c.check_type.includes(needle),
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
      <ErrorBox error={error} />
      {isLoading ? <Spinner /> : <ChecksTable checks={shown} />}
    </div>
  );
}
