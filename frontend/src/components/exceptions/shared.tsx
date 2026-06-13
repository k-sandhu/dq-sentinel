// Shared constants, URL<->filter helpers, and minimal local pills for the
// exceptions triage workspace (#63).
//
// Pills: #58 owns the canonical StatusPill/SeverityBadge + pill CSS. Until that
// lands we use the pre-existing `Pill` from ui.tsx (already styles every
// exception status) and a minimal local severity badge built from the existing
// `.sev` dot tokens — no edits to ui.tsx or shared pill CSS. Swap to #58's
// components in a follow-up.

import type { ExceptionStatus, Severity } from "../../api/types";
import { Pill } from "../ui";

export type GroupMode = "none" | "check" | "dataset";
export type SeenSince = "" | "24h" | "7d" | "30d";

export const ALL_STATUSES: ExceptionStatus[] = [
  "open",
  "acknowledged",
  "expected",
  "resolved",
  "muted",
];
export const ALL_SEVERITIES: Severity[] = ["error", "warn", "info"];

// Triage action hints (ported from the old ExceptionsTriage component) — the
// keyboard shortcut letter doubles as discoverability in the button title.
export const TRIAGE_ACTIONS: {
  status: ExceptionStatus;
  label: string;
  hint: string;
  key: string;
}[] = [
  { status: "acknowledged", label: "Acknowledge", hint: "Seen, investigation pending", key: "a" },
  { status: "expected", label: "Expected", hint: "Legitimate data — reference for future", key: "e" },
  { status: "resolved", label: "Resolve", hint: "Underlying data fixed", key: "r" },
  { status: "muted", label: "Mute", hint: "Stop counting this one", key: "m" },
  { status: "open", label: "Reopen", hint: "Back to the queue", key: "u" },
];

export const SEEN_SINCE_OPTIONS: { key: SeenSince; label: string }[] = [
  { key: "", label: "Any time" },
  { key: "24h", label: "Last 24h" },
  { key: "7d", label: "Last 7 days" },
  { key: "30d", label: "Last 30 days" },
];

export const SORT_OPTIONS: { key: string; label: string }[] = [
  { key: "newest", label: "Newest" },
  { key: "oldest", label: "Oldest" },
  { key: "occurrences", label: "Most recurring" },
  { key: "severity", label: "Severity" },
];

export const PAGE_SIZE = 50;
export const SELECTION_CAP = 1000; // mirror the API bulk cap (#56)

/** Convert a `seen_since` preset key to an ISO timestamp at request time. */
export function seenSinceToIso(preset: SeenSince): string | null {
  if (!preset) return null;
  const hours = preset === "24h" ? 24 : preset === "7d" ? 24 * 7 : 24 * 30;
  return new Date(Date.now() - hours * 3600 * 1000).toISOString();
}

/** Parsed workspace filter state (mirrors the API params + UI-only `group`/`sel`). */
export interface WorkspaceFilters {
  status: string[];
  severity: string[];
  check_type: string;
  assignee: string; // "" | "me" | "none" | "<id>"
  recurrence: string; // "" | "new" | "recurring"
  seen_since: SeenSince;
  q: string;
  sort: string;
  offset: number;
  group: GroupMode;
  sel: number | null; // open panel's exception id
}

export function parseFilters(sp: URLSearchParams): WorkspaceFilters {
  const selRaw = sp.get("sel");
  return {
    status: sp.getAll("status"),
    severity: sp.getAll("severity"),
    check_type: sp.get("check_type") ?? "",
    assignee: sp.get("assignee") ?? "",
    recurrence: sp.get("recurrence") ?? "",
    seen_since: (sp.get("seen_since") as SeenSince) ?? "",
    q: sp.get("q") ?? "",
    sort: sp.get("sort") ?? "newest",
    offset: Number(sp.get("offset") ?? "0") || 0,
    group: (sp.get("group") as GroupMode) ?? "none",
    sel: selRaw ? Number(selRaw) : null,
  };
}

/** Build the API query string for list/facets/export from filter state.
 *  Pinned props (datasetId/runId/checkId) are added by the caller. `seen_since`
 *  is converted to ISO; `group`/`sel`/`offset` are list-only concerns. */
export function toApiParams(
  f: WorkspaceFilters,
  opts: { datasetId?: number; runId?: number; checkId?: number } = {},
): URLSearchParams {
  const p = new URLSearchParams();
  if (opts.datasetId) p.set("dataset_id", String(opts.datasetId));
  if (opts.runId) p.set("run_id", String(opts.runId));
  if (opts.checkId) p.set("check_id", String(opts.checkId));
  for (const s of f.status) p.append("status", s);
  for (const s of f.severity) p.append("severity", s);
  if (f.check_type) p.set("check_type", f.check_type);
  if (f.assignee) p.set("assignee", f.assignee);
  if (f.recurrence) p.set("recurrence", f.recurrence);
  const iso = seenSinceToIso(f.seen_since);
  if (iso) p.set("seen_since", iso);
  if (f.q) p.set("q", f.q);
  if (f.sort && f.sort !== "newest") p.set("sort", f.sort);
  return p;
}

/** Minimal local severity badge (uses existing `.sev` tokens; not #58's). */
export function SevBadge({ severity }: { severity: string | null | undefined }) {
  const sev = severity || "info";
  return (
    <span className="xw-sev" title={`${sev} severity`}>
      <span className={`sev ${sev}`} />
      {sev}
    </span>
  );
}

/** Status pill — reuses the pre-existing ui.tsx Pill (styles all statuses). */
export function StatusPill({ status }: { status: ExceptionStatus | string }) {
  return <Pill value={status} />;
}
