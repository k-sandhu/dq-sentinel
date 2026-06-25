// Shared types + helpers for the Workbench worksheet (D4 / FE-2). Kept free of JSX
// so the worksheet brain — tab shape, row-limit ladder, save-query permissions — is
// unit-testable and reused across the extracted Workbench pieces.

import type { QueryRunResult, SavedQuery, User, VizType } from "../../api/types";
import { isAdmin } from "../../auth";
import { newTabId } from "../../lib/workbenchTabs";

/** One worksheet tab. Results/errors live in memory; only id/title/sql persist. */
export interface TabState {
  id: string;
  sql: string;
  dirty: boolean;
  result: QueryRunResult | null;
  error: string | null;
  resultLimit: number; // the LIMIT the current result ran at — drives the truncation affordance
  view: "table" | "chart";
  chart: { type: VizType; x: string; y: string };
  showFilters: boolean;
}

export function makeTab(sql = ""): TabState {
  return {
    id: newTabId(),
    sql,
    dirty: false,
    result: null,
    error: null,
    resultLimit: 200,
    view: "table",
    chart: { type: "bar", x: "", y: "" },
    showFilters: false,
  };
}

export const LIMITS = [50, 200, 500, 1000, 2000];

/** The next row cap above `current` from the LIMITS ladder, clamped at 2000 (max). */
export function nextLimitAfter(current: number): number {
  return LIMITS.find((n) => n > current) ?? 2000;
}

export function copyText(text: string): void {
  navigator.clipboard?.writeText(text).catch(() => {
    /* clipboard blocked — silently ignore */
  });
}

/** A saved query is editable/deletable by its creator or any admin. */
export function canManageQuery(user: User | null, q: SavedQuery): boolean {
  return isAdmin(user) || (!!user && q.created_by_id === user.id);
}
