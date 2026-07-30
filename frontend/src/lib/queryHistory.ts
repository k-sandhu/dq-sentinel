// Per-user Workbench query history (#104). Client-side only — every executed query
// (ok or error) is appended so the analyst can re-run recent work without having
// saved it. Server-side history is a noted follow-up. Newest first, capped to keep
// the list bounded.
//
// Storage goes through the prefs chokepoint (#294) rather than localStorage
// directly: this list holds the raw SQL an analyst typed, so it MUST land in that
// user's namespace and not be inherited by the next person on a shared machine.

import { getPref, PREF_KEYS, setPref } from "./prefs";

export interface QueryHistoryEntry {
  id: string;
  connectionId: number;
  connectionName: string;
  sql: string;
  ranAt: string; // ISO timestamp
  rowCount: number | null;
  elapsedMs: number | null;
  ok: boolean;
  error: string | null;
}

const CAP = 50;

export function loadHistory(): QueryHistoryEntry[] {
  const parsed = getPref<unknown>(PREF_KEYS.workbenchHistory, []);
  return Array.isArray(parsed) ? (parsed as QueryHistoryEntry[]) : [];
}

function persist(entries: QueryHistoryEntry[]): void {
  // No component subscribes to history via `dq:prefs` — it is re-read explicitly
  // by the page that wrote it — so skip the broadcast.
  setPref(PREF_KEYS.workbenchHistory, entries.slice(0, CAP), { notify: false });
}

/** Prepend a freshly executed query and return the new (capped) list. */
export function addHistory(
  entry: Omit<QueryHistoryEntry, "id" | "ranAt">,
): QueryHistoryEntry[] {
  const full: QueryHistoryEntry = {
    ...entry,
    id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    ranAt: new Date().toISOString(),
  };
  const next = [full, ...loadHistory()].slice(0, CAP);
  persist(next);
  return next;
}

export function clearHistory(): QueryHistoryEntry[] {
  persist([]);
  return [];
}
