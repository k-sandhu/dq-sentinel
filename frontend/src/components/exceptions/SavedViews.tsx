// Saved-view chips: built-ins + user-saved (localStorage). Status/severity-based
// chips show facet-count badges computed client-side from the facet response;
// other chips (e.g. assignee=me) omit a count in v1 (#63).

import { useState } from "react";
import type { ExceptionFacets } from "../../api/types";
import { getPref, PREF_KEYS, setPref } from "../../lib/prefs";
import type { SavedView } from "../../lib/prefs";

interface BuiltIn {
  name: string;
  params: string;
  /** how to derive a count from facets, or null when not derivable in v1 */
  count?: (f: ExceptionFacets) => number | null;
}

const BUILTINS: BuiltIn[] = [
  { name: "My open", params: "assignee=me&status=open" }, // needs its own call; omit count v1
  {
    name: "New today",
    params: "recurrence=new&status=open",
    count: (f) => f.status.open ?? null,
  },
  {
    name: "High severity",
    params: "severity=error&status=open",
    count: (f) => f.severity.error ?? null,
  },
  { name: "Recurring", params: "recurrence=recurring&status=open" },
  { name: "Unassigned", params: "assignee=none&status=open" },
  { name: "Expected", params: "status=expected", count: (f) => f.status.expected ?? null },
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
  facets,
  onApply,
}: {
  currentParams: string;
  facets: ExceptionFacets | undefined;
  onApply: (params: string) => void;
}) {
  const [views, setViews] = useState<SavedView[]>(() =>
    getPref<SavedView[]>(PREF_KEYS.views, []),
  );

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
        const count = facets && b.count ? b.count(facets) : null;
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
