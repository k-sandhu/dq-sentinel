// Multi-tab editor state for the Workbench (#104). Tabs are local worksheets; only
// their id/title/sql persist (results stay in memory). Restoring the last session is
// a nicety, so all access is best-effort and tolerant of bad data.
//
// Persistence goes through the prefs chokepoint (#294) so worksheets land in the
// signed-in user's namespace — un-namespaced, the next analyst on a shared machine
// opened the Workbench to someone else's in-progress SQL.

import { getPref, PREF_KEYS, setPref } from "./prefs";

export interface WorkbenchTab {
  id: string;
  title: string;
  sql: string;
}

export interface WorkbenchTabsState {
  tabs: WorkbenchTab[];
  activeId: string;
  /** The connection the worksheets were written against (#255). The Workbench
   *  runs every tab against ONE selected source, so this belongs to the session,
   *  not to a tab. Restoring it lets the page tell "resume where I left off"
   *  (same source) from "this SQL targets another database" (a dataset link or
   *  `?connection_id=` landing on a different source). `null` when unknown —
   *  a pre-#255 saved state, or a session that never resolved a connection. */
  connectionId: number | null;
}

const CAP = 12;

export function newTabId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** A short, human title derived from the SQL (first non-empty line), used when the
 *  analyst hasn't named the tab. Falls back to a positional "Query N". */
export function deriveTabTitle(sql: string, index: number): string {
  const firstLine = sql
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l.length > 0);
  if (!firstLine) return `Query ${index + 1}`;
  return firstLine.length > 28 ? `${firstLine.slice(0, 28)}…` : firstLine;
}

export function loadTabsState(): WorkbenchTabsState | null {
  const parsed = getPref<Partial<WorkbenchTabsState> | null>(PREF_KEYS.workbenchTabs, null);
  if (!parsed || !Array.isArray(parsed.tabs) || parsed.tabs.length === 0) return null;
  const tabs = parsed.tabs
    .filter((t): t is WorkbenchTab => !!t && typeof t.id === "string" && typeof t.sql === "string")
    .slice(0, CAP);
  if (tabs.length === 0) return null;
  const activeId = tabs.some((t) => t.id === parsed.activeId) ? parsed.activeId! : tabs[0].id;
  const connectionId =
    typeof parsed.connectionId === "number" && Number.isFinite(parsed.connectionId)
      ? parsed.connectionId
      : null;
  return { tabs, activeId, connectionId };
}

export function persistTabsState(state: WorkbenchTabsState): void {
  // Called on every keystroke in the SQL editor — deliberately silent so the
  // `dq:prefs` subscribers (sidebar favorites, recents strips) aren't woken per
  // character. Nothing outside the Workbench reads this key.
  setPref(
    PREF_KEYS.workbenchTabs,
    { tabs: state.tabs.slice(0, CAP), activeId: state.activeId, connectionId: state.connectionId },
    { notify: false },
  );
}
