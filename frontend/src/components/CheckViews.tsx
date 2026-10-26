import { useSearchParams } from "react-router";
import type { Check } from "../api/types";
import "../styles/check-results.css";

export type CheckView = "results" | "proposals";

/** Shared on both check routes. Keep old proposal bookmarks working. */
export function checkView(params: URLSearchParams): CheckView {
  const view = params.get("view");
  return view === "proposals" || (view !== "results" && params.get("status") === "proposed")
    ? "proposals" : "results";
}

export function useCheckView() {
  const [params, setParams] = useSearchParams();
  const view = checkView(params);
  const setView = (next: CheckView, generated = false) => {
    const updated = new URLSearchParams(params);
    updated.set("view", next);
    // Proposals ignores Results filters, but preserves them for the return
    // trip. Remove only the incompatible legacy proposal status.
    if (updated.get("status") === "proposed") updated.delete("status");
    if (generated) {
      updated.delete("status");
      updated.delete("q");
      updated.delete("last_status");
    }
    setParams(updated);
  };
  return { view, setView };
}

export function CheckViewSwitch({ view, proposals, onChange }: {
  view: CheckView; proposals: number; onChange: (view: CheckView) => void;
}) {
  return <div className="check-view-switch" role="group" aria-label="Check view">
    <button type="button" aria-pressed={view === "results"} onClick={() => onChange("results")}>Results</button>
    <button type="button" aria-pressed={view === "proposals"} onClick={() => onChange("proposals")}>Proposals ({proposals})</button>
  </div>;
}

function priority(check: Check): number {
  return ({ error: 0, fail: 1, warn: 2, pass: 4 } as Record<string, number>)[check.last_status ?? ""] ?? 3;
}

/** Urgency first, then column within each urgency band: passing rows never
 * separate a failure from another failure, even across different columns. */
export function orderCheckResults(checks: Check[], showDataset: boolean): Check[] {
  return [...checks].sort((a, b) => priority(a) - priority(b)
    || (showDataset ? a.dataset_name.localeCompare(b.dataset_name) : 0)
    || (a.column_name ?? "").localeCompare(b.column_name ?? "")
    || a.name.localeCompare(b.name)
    || a.id - b.id);
}

export function resultGroup(check: Check, showDataset: boolean): string {
  const outcome = ({ error: "Run errors", fail: "Failing", warn: "Warnings", pass: "Passing" } as Record<string, string>)[check.last_status ?? ""] ?? "No known result";
  return `${outcome} · ${showDataset ? `${check.dataset_name} · ` : ""}${check.column_name ?? "Table checks"}`;
}
