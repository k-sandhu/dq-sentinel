// Per-browser Workbench query history (#104). Stored in localStorage only — every
// executed query (ok or error) is appended so the analyst can re-run recent work
// without having saved it. This is deliberately client-side; server-side history
// is a noted follow-up. Newest first, capped to keep the list bounded.

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

const KEY = "dq-workbench-history";
const CAP = 50;

export function loadHistory(): QueryHistoryEntry[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as QueryHistoryEntry[]) : [];
  } catch {
    return [];
  }
}

function persist(entries: QueryHistoryEntry[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(entries.slice(0, CAP)));
  } catch {
    /* storage unavailable / quota exceeded — history is best-effort */
  }
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
