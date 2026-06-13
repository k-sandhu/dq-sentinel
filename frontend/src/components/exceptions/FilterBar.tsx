// Filter controls + facet counts + debounced search + CSV export + clear-all.
// Status/severity are multi-select chips with counts; the rest are selects (#63).

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, getToken } from "../../api/client";
import type { Assignee, CheckTypeInfo, ExceptionFacets } from "../../api/types";
import { checkTypeLabel } from "../../lib/checkMeta";
import { ALL_SEVERITIES, ALL_STATUSES, SEEN_SINCE_OPTIONS, SORT_OPTIONS } from "./shared";
import type { SeenSince, WorkspaceFilters } from "./shared";

export default function FilterBar({
  filters,
  facets,
  assignees,
  exportUrl,
  update,
  clearAll,
}: {
  filters: WorkspaceFilters;
  facets: ExceptionFacets | undefined;
  assignees: Assignee[];
  exportUrl: string; // query string (without leading ?) for export.csv
  update: (patch: Partial<WorkspaceFilters>) => void;
  clearAll: () => void;
}) {
  // Debounced search: keep the input snappy, hit the API at 300ms (matches the
  // server's count-cost mitigation note in #57).
  const [qLocal, setQLocal] = useState(filters.q);
  useEffect(() => setQLocal(filters.q), [filters.q]);
  useEffect(() => {
    const t = setTimeout(() => {
      if (qLocal !== filters.q) update({ q: qLocal, offset: 0 });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qLocal]);

  const { data: checkTypes } = useQuery({
    queryKey: ["check-types"],
    queryFn: () => api.get<CheckTypeInfo[]>("/checks/types"),
  });

  const [exporting, setExporting] = useState(false);
  async function exportCsv() {
    setExporting(true);
    try {
      // window.open can't carry the auth header; fetch with the token, blob it.
      const resp = await fetch(`/api/v1/exceptions/export.csv?${exportUrl}`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "exceptions.csv";
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  }

  function toggleArr(key: "status" | "severity", value: string) {
    const cur = filters[key];
    const next = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
    update({ [key]: next, offset: 0 } as Partial<WorkspaceFilters>);
  }

  return (
    <div className="xw-filterbar">
      <div className="xw-filter-chips">
        {ALL_STATUSES.map((s) => {
          const c = facets?.status[s];
          return (
            <button
              key={s}
              className={`filter-chip${filters.status.includes(s) ? " on" : ""}`}
              aria-pressed={filters.status.includes(s)}
              onClick={() => toggleArr("status", s)}
            >
              {s}
              {c != null && <span className="xw-chip-count">{c}</span>}
            </button>
          );
        })}
      </div>

      <div className="xw-filter-chips">
        {ALL_SEVERITIES.map((s) => {
          const c = facets?.severity[s];
          return (
            <button
              key={s}
              className={`filter-chip${filters.severity.includes(s) ? " on" : ""}`}
              aria-pressed={filters.severity.includes(s)}
              onClick={() => toggleArr("severity", s)}
            >
              {s}
              {c != null && <span className="xw-chip-count">{c}</span>}
            </button>
          );
        })}
      </div>

      <div className="xw-filter-selects">
        {/* Dataset filtering is handled by the page-level control (ExceptionsPage)
            and is pinned when embedded on a dataset's Exceptions tab. */}
        <select
          aria-label="Filter by check type"
          value={filters.check_type}
          onChange={(e) => update({ check_type: e.target.value, offset: 0 })}
        >
          <option value="">All types</option>
          {checkTypes?.map((t) => (
            <option key={t.key} value={t.key}>
              {checkTypeLabel(t.key)}
            </option>
          ))}
        </select>

        <select
          aria-label="Filter by recurrence"
          value={filters.recurrence}
          onChange={(e) => update({ recurrence: e.target.value, offset: 0 })}
        >
          <option value="">Any recurrence</option>
          <option value="new">New (last 24h)</option>
          <option value="recurring">Recurring</option>
        </select>

        <select
          aria-label="Filter by assignee"
          value={filters.assignee}
          onChange={(e) => update({ assignee: e.target.value, offset: 0 })}
        >
          <option value="">Anyone</option>
          <option value="me">Me</option>
          <option value="none">Unassigned</option>
          {assignees.map((a) => (
            <option key={a.id} value={String(a.id)}>
              {a.name || a.email}
            </option>
          ))}
        </select>

        <select
          aria-label="Filter by time last seen"
          value={filters.seen_since}
          onChange={(e) => update({ seen_since: e.target.value as SeenSince, offset: 0 })}
        >
          {SEEN_SINCE_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>

        <select
          aria-label="Sort order"
          value={filters.sort}
          onChange={(e) => update({ sort: e.target.value, offset: 0 })}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>

        <select
          aria-label="Group rows"
          value={filters.group}
          onChange={(e) => update({ group: e.target.value as WorkspaceFilters["group"] })}
        >
          <option value="none">No grouping</option>
          <option value="check">Group by check</option>
          <option value="dataset">Group by dataset</option>
        </select>
      </div>

      <div className="xw-filter-search">
        <input
          type="text"
          placeholder="Search reason, note, or check…"
          aria-label="Search exceptions"
          value={qLocal}
          onChange={(e) => setQLocal(e.target.value)}
        />
        <button className="small" onClick={exportCsv} disabled={exporting} title="Export the current view to CSV">
          {exporting ? "Exporting…" : "Export CSV"}
        </button>
        <button className="small ghost" onClick={clearAll} title="Clear all filters">
          Clear all
        </button>
      </div>
    </div>
  );
}
