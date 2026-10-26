// Compact primary filters, progressively disclosed advanced filters, and view tools.
// Filter state remains URL-owned; disclosure alone never changes the query (#326).

import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Assignee, CheckTypeInfo, ExceptionFacets } from "../../api/types";
import { checkTypeLabel } from "../../lib/checkMeta";
import { ALL_SEVERITIES, ALL_STATUSES, SEEN_SINCE_OPTIONS, SORT_OPTIONS } from "./shared";
import type { SeenSince, WorkspaceFilters } from "./shared";
import "../../styles/exception-filters.css";

/** Turn an export failure into something the analyst can act on (#294).
 *  A silent failure is worse than a slow one: the button used to slide back to
 *  "Export CSV" on a 403/500/offline and the analyst assumed a file downloaded. */
export function exportErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) {
      return "Export failed: you don't have permission to export exceptions. Ask an admin for editor access.";
    }
    if (err.status === 401) {
      return "Export failed: your session expired. Sign in again, then retry the export.";
    }
    if (err.status >= 500) {
      return `Export failed: the server errored (${err.status}). Retry, or narrow the filters if this view is very large.`;
    }
    return `Export failed: ${err.message}`;
  }
  return "Export failed: couldn't reach the server. Check your connection and retry.";
}

export default function FilterBar({
  filters,
  facets,
  assignees,
  exportUrl,
  update,
  clearAll,
  onError,
}: {
  filters: WorkspaceFilters;
  facets: ExceptionFacets | undefined;
  assignees: Assignee[];
  exportUrl: string; // query string (without leading ?) for export.csv
  update: (patch: Partial<WorkspaceFilters>) => void;
  clearAll: () => void;
  /** Surface a failure through the workspace toast (role="status"). */
  onError: (message: string) => void;
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
    queryKey: qk.checkTypes.list(),
    queryFn: () => api.get<CheckTypeInfo[]>("/checks/types"),
  });

  const [exporting, setExporting] = useState(false);
  async function exportCsv() {
    setExporting(true);
    try {
      await api.download(`/exceptions/export.csv?${exportUrl}`, "exceptions.csv");
    } catch (err) {
      onError(exportErrorMessage(err));
    } finally {
      setExporting(false);
    }
  }

  function toggleArr(key: "status" | "severity", value: string) {
    const cur = filters[key];
    const next = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
    update({ [key]: next, offset: 0 } as Partial<WorkspaceFilters>);
  }

  const [advanced, setAdvanced] = useState(false);
  const advancedId = useId();
  const statusPickerRef = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    function dismissOutside(event: PointerEvent) {
      const picker = statusPickerRef.current;
      if (picker?.open && event.target instanceof Node && !picker.contains(event.target)) {
        picker.open = false;
      }
    }
    document.addEventListener("pointerdown", dismissOutside);
    return () => document.removeEventListener("pointerdown", dismissOutside);
  }, []);
  const chips: { label: string; patch: Partial<WorkspaceFilters> }[] = [
    ...filters.severity.map((s) => ({ label: `Severity: ${s}${facets?.severity[s] != null ? ` (${facets.severity[s]})` : ""}`, patch: { severity: filters.severity.filter((v) => v !== s) } })),
    ...(filters.check_type ? [{ label: `Type: ${checkTypeLabel(filters.check_type)}`, patch: { check_type: "" } }] : []),
    ...(filters.recurrence ? [{ label: filters.recurrence === "new" ? "New (last 24h)" : "Recurring", patch: { recurrence: "" } }] : []),
    ...(filters.seen_since ? [{ label: `Seen: ${SEEN_SINCE_OPTIONS.find((o) => o.key === filters.seen_since)?.label ?? filters.seen_since}`, patch: { seen_since: "" as SeenSince } }] : []),
  ];

  return (
    <div className="xw-filterbar xw-compact-filters">
      <div className="xw-primary-filters">
        <input type="search" placeholder="Search reason, note, or check…" aria-label="Search exceptions"
          value={qLocal} onChange={(e) => setQLocal(e.target.value)} />
        <details className="xw-status-picker" ref={statusPickerRef}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
          }}
          onKeyDown={(event) => {
            if (event.key !== "Escape" || !event.currentTarget.open) return;
            event.preventDefault();
            event.stopPropagation();
            event.currentTarget.open = false;
            event.currentTarget.querySelector("summary")?.focus();
          }}>
          <summary>Status: {filters.status.length ? filters.status.join(", ") : "All"}</summary>
          <div className="xw-status-options" role="group" aria-label="Filter by status">
            {ALL_STATUSES.map((s) => (
              <label key={s}>
                <input type="checkbox" checked={filters.status.includes(s)} onChange={() => toggleArr("status", s)} />
                {s}{facets?.status[s] != null && <span className="xw-chip-count">{facets.status[s]}</span>}
              </label>
            ))}
          </div>
        </details>
        <select aria-label="Filter by assignee" value={filters.assignee}
          onChange={(e) => update({ assignee: e.target.value, offset: 0 })}>
          <option value="">Anyone</option><option value="me">Me</option><option value="none">Unassigned</option>
          {assignees.map((a) => <option key={a.id} value={String(a.id)}>{a.name || a.email}</option>)}
        </select>
        <button className="small" aria-expanded={advanced} aria-controls={advancedId} onClick={() => setAdvanced(!advanced)}>
          More filters{chips.length > 0 ? ` (${chips.length})` : ""}
        </button>
        <button className="small ghost" onClick={() => { setQLocal(""); clearAll(); }} title="Clear all filters">Clear all</button>
      </div>
      {chips.length > 0 && <div className="xw-active-filters" aria-label="Active advanced filters">
        {chips.map((chip) => <button key={chip.label} className="small" aria-label={`Remove ${chip.label}`}
          onClick={() => update({ ...chip.patch, offset: 0 })}>{chip.label} <span aria-hidden="true">×</span></button>)}
      </div>}
      {advanced && <div id={advancedId} className="xw-advanced-filters" role="region" aria-label="More exception filters">
        <fieldset><legend>Severity</legend><div className="xw-filter-chips">
          {ALL_SEVERITIES.map((s) => <button key={s} className={`filter-chip${filters.severity.includes(s) ? " on" : ""}`}
            aria-pressed={filters.severity.includes(s)} onClick={() => toggleArr("severity", s)}>
            {s}{facets?.severity[s] != null && <span className="xw-chip-count">{facets.severity[s]}</span>}
          </button>)}
        </div></fieldset>
        <label>Check type<select aria-label="Filter by check type" value={filters.check_type}
          onChange={(e) => update({ check_type: e.target.value, offset: 0 })}>
          <option value="">All types</option>
          {checkTypes?.map((t) => <option key={t.key} value={t.key}>{checkTypeLabel(t.key)}</option>)}
        </select></label>
        <label>Recurrence<select aria-label="Filter by recurrence" value={filters.recurrence}
          onChange={(e) => update({ recurrence: e.target.value, offset: 0 })}>
          <option value="">Any recurrence</option><option value="new">New (last 24h)</option><option value="recurring">Recurring</option>
        </select></label>
        <label>Last seen<select aria-label="Filter by time last seen" value={filters.seen_since}
          onChange={(e) => update({ seen_since: e.target.value as SeenSince, offset: 0 })}>
          {SEEN_SINCE_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select></label>
      </div>}
      <div className="xw-view-options">
        <select aria-label="Sort order" value={filters.sort} onChange={(e) => update({ sort: e.target.value, offset: 0 })}>
          {SORT_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select>
        <select aria-label="Group rows" value={filters.group} onChange={(e) => update({ group: e.target.value as WorkspaceFilters["group"] })}>
          <option value="none">No grouping</option><option value="check">Group by check</option><option value="dataset">Group by dataset</option>
        </select>
        <button className="small" onClick={exportCsv} disabled={exporting} title="Export the current view to CSV">{exporting ? "Exporting…" : "Export CSV"}</button>
      </div>
    </div>
  );
}
