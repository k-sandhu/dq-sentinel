import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Check } from "../api/types";
import ChecksTable from "../components/ChecksTable";
import { ErrorBox, Spinner } from "../components/ui";

const FILTERS = ["all", "active", "proposed", "disabled"] as const;

export default function ChecksPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const { data, isLoading, error } = useQuery({
    queryKey: ["checks", { filter }],
    queryFn: () => api.get<Check[]>(`/checks${filter === "all" ? "" : `?status=${filter}`}`),
  });

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Checks</h1>
          <div className="sub">Every rule guarding your data, across all datasets</div>
        </div>
      </div>
      <div className="toolbar">
        <div className="chip-row">
          {FILTERS.map((f) => (
            <button key={f} className={`filter-chip${filter === f ? " on" : ""}`} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
      </div>
      <ErrorBox error={error} />
      {isLoading ? <Spinner /> : <ChecksTable checks={data ?? []} />}
    </div>
  );
}
