// Multi-tab editor state for the Workbench (#104). Tabs are local worksheets; only
// their id/title/sql persist to localStorage (results stay in memory). Restoring the
// last session is a nicety, so all access is best-effort and tolerant of bad data.

export interface WorkbenchTab {
  id: string;
  title: string;
  sql: string;
}

export interface WorkbenchTabsState {
  tabs: WorkbenchTab[];
  activeId: string;
}

const KEY = "dq-workbench-tabs";
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
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<WorkbenchTabsState>;
    if (!parsed || !Array.isArray(parsed.tabs) || parsed.tabs.length === 0) return null;
    const tabs = parsed.tabs
      .filter((t): t is WorkbenchTab => !!t && typeof t.id === "string" && typeof t.sql === "string")
      .slice(0, CAP);
    if (tabs.length === 0) return null;
    const activeId = tabs.some((t) => t.id === parsed.activeId) ? parsed.activeId! : tabs[0].id;
    return { tabs, activeId };
  } catch {
    return null;
  }
}

export function persistTabsState(state: WorkbenchTabsState): void {
  try {
    localStorage.setItem(
      KEY,
      JSON.stringify({ tabs: state.tabs.slice(0, CAP), activeId: state.activeId }),
    );
  } catch {
    /* storage unavailable — tabs still work for this session */
  }
}
