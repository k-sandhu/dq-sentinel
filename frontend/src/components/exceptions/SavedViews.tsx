// Saved-view chips: built-ins + user-saved (localStorage). A built-in chip
// REPLACES the current filters when clicked, so its badge must be the absolute
// count of the view's own params — GET /exceptions/view-counts serves exactly
// that. (Deriving badges from the filter-relative /facets response showed
// numbers the click didn't reproduce — UX benchmark P1.)

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { ExceptionViewCounts } from "../../api/types";
import { getPref, PREF_KEYS, setPref } from "../../lib/prefs";
import type { SavedView } from "../../lib/prefs";

interface BuiltIn {
  name: string;
  params: string;
  /** field of GET /exceptions/view-counts holding this view's absolute count */
  countKey: keyof ExceptionViewCounts;
}

const BUILTINS: BuiltIn[] = [
  { name: "My open", params: "assignee=me&status=open", countKey: "my_open" },
  { name: "New today", params: "recurrence=new&status=open", countKey: "new_today" },
  { name: "High severity", params: "severity=error&status=open", countKey: "high_severity" },
  { name: "Recurring", params: "recurrence=recurring&status=open", countKey: "recurring" },
  { name: "Unassigned", params: "assignee=none&status=open", countKey: "unassigned" },
  { name: "Expected", params: "status=expected", countKey: "expected" },
];

/** Two param-strings represent the same view if their sorted entries match. */
function sameView(a: string, b: string): boolean {
  const norm = (s: string) =>
    [...new URLSearchParams(s).entries()]
      .filter(([k]) => k !== "offset" && k !== "sel" && k !== "group" && k !== "sort")
      .map(([k, v]) => `${k}=${v}`)
      .sort()
      .join("&");
  return norm(a) === norm(b);
}

export default function SavedViews({
  currentParams,
  onApply,
}: {
  currentParams: string;
  onApply: (params: string) => void;
}) {
  const [views, setViews] = useState<SavedView[]>(() =>
    getPref<SavedView[]>(PREF_KEYS.views, []),
  );

  // Absolute per-view counts; triage mutations invalidate this key so the
  // badges track the queue. Badges are hidden (not zeroed) while loading.
  const { data: counts } = useQuery({
    queryKey: qk.exceptionViewCounts.get(),
    queryFn: () => api.get<ExceptionViewCounts>("/exceptions/view-counts"),
    staleTime: 15_000,
  });

  function persist(next: SavedView[]) {
    setViews(next);
    setPref(PREF_KEYS.views, next);
  }

  function saveCurrent() {
    const name = window.prompt("Name this view");
    if (!name) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    const next = [...views.filter((v) => v.name !== trimmed), { name: trimmed, params: currentParams }];
    persist(next);
  }

  function remove(name: string) {
    persist(views.filter((v) => v.name !== name));
  }

  return (
    <div className="xw-views" role="tablist" aria-label="Saved views">
      {BUILTINS.map((b) => {
        const active = sameView(b.params, currentParams);
        const count = counts ? counts[b.countKey] : null;
        return (
          <button
            key={b.name}
            role="tab"
            aria-selected={active}
            className={`xw-view-chip${active ? " on" : ""}`}
            onClick={() => onApply(b.params)}
          >
            {b.name}
            {count != null && <span className="xw-view-count">{count}</span>}
          </button>
        );
      })}
      <span className="xw-views-sep" aria-hidden />
      {views.map((v) => {
        const active = sameView(v.params, currentParams);
        return (
          <span key={v.name} className={`xw-view-chip saved${active ? " on" : ""}`}>
            <button
              className="xw-view-chip-main"
              role="tab"
              aria-selected={active}
              onClick={() => onApply(v.params)}
            >
              {v.name}
            </button>
            <button
              className="xw-view-del"
              aria-label={`Delete saved view ${v.name}`}
              title="Delete saved view"
              onClick={() => remove(v.name)}
            >
              ×
            </button>
          </span>
        );
      })}
      <button className="xw-view-save small ghost" onClick={saveCurrent} title="Save the current filters as a view">
        + Save view
      </button>
    </div>
  );
}
