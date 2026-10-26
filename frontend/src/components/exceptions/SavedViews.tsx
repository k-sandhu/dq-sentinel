// Saved-view picker: built-ins + user-saved (localStorage). A built-in view
// REPLACES the current filters when selected, so its option shows the absolute
// count of the view's own params — GET /exceptions/view-counts serves exactly
// that. (Deriving counts from the filter-relative /facets response showed
// numbers the click didn't reproduce — UX benchmark P1.)

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { ExceptionViewCounts } from "../../api/types";
import { getPref, PREF_KEYS, setPref } from "../../lib/prefs";
import type { SavedView } from "../../lib/prefs";
import { useConfirm } from "../confirm";
import { useUnsavedGuard } from "../../lib/useUnsavedGuard";

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

/** Built-ins describe predicates; personal views also restore presentation. */
function sameView(a: string, b: string, presentation = false): boolean {
  const norm = (s: string) =>
    [...new URLSearchParams(s).entries()]
      .filter(([k, v]) => k !== "offset" && k !== "sel"
        && (presentation ? !((k === "group" && v === "none") || (k === "sort" && v === "newest")) : k !== "group" && k !== "sort"))
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .sort()
      .join("&");
  return norm(a) === norm(b);
}

export default function SavedViews({
  currentParams,
  pinned,
  onApply,
}: {
  currentParams: string;
  /** Workspace embedding scope (dataset tab pins these outside the URL) —
   *  badges must count within it or they stop matching what a click shows. */
  pinned?: { datasetId?: number; runId?: number; checkId?: number };
  onApply: (params: string) => void;
}) {
  const [views, setViews] = useState<SavedView[]>(() =>
    getPref<SavedView[]>(PREF_KEYS.views, []),
  );
  // Names are unique in persisted views. Retain the explicit choice even when
  // two aliases have identical params; array indexes change after deletion.
  const [chosenView, setChosenView] = useState("");

  const pinnedQs = new URLSearchParams();
  if (pinned?.datasetId != null) pinnedQs.set("dataset_id", String(pinned.datasetId));
  if (pinned?.runId != null) pinnedQs.set("run_id", String(pinned.runId));
  if (pinned?.checkId != null) pinnedQs.set("check_id", String(pinned.checkId));
  const pinnedParams = pinnedQs.toString();

  // Absolute per-view counts within the pinned scope. Triage in this tab
  // invalidates the key; the 30s poll matches the list/facets queries so
  // worker- and teammate-made changes update the badges too (codex review).
  const { data: counts } = useQuery({
    queryKey: qk.exceptionViewCounts.get(pinnedParams),
    queryFn: () =>
      api.get<ExceptionViewCounts>(
        `/exceptions/view-counts${pinnedParams ? `?${pinnedParams}` : ""}`,
      ),
    refetchInterval: 30_000,
  });

  function persist(next: SavedView[]) {
    setViews(next);
    setPref(PREF_KEYS.views, next);
  }

  const confirm = useConfirm();
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  useUnsavedGuard(saving && name.trim().length > 0);

  async function saveCurrent() {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (views.some((v) => v.name === trimmed) && !await confirm({ title: `Replace saved view ${trimmed}?`, confirmLabel: "Replace" })) return;
    const next = [...views.filter((v) => v.name !== trimmed), { name: trimmed, params: currentParams }];
    persist(next);
    setChosenView(`saved:${trimmed}`);
    setName("");
    setSaving(false);
  }

  async function remove(name: string) {
    if (await confirm({ title: `Delete saved view ${name}?`, confirmLabel: "Delete", danger: true })) {
      persist(views.filter((v) => v.name !== name));
    }
  }

  const activeBuiltIn = BUILTINS.findIndex((b) => sameView(b.params, currentParams));
  const explicitSaved = chosenView.startsWith("saved:")
    ? views.find((v) => v.name === chosenView.slice(6) && sameView(v.params, currentParams, true)) : undefined;
  const explicitBuiltIn = chosenView.startsWith("builtin:")
    ? BUILTINS.find((b) => b.countKey === chosenView.slice(8) && sameView(b.params, currentParams)) : undefined;
  const matchedSaved = views.find((v) => sameView(v.params, currentParams, true));
  const activeSaved = explicitSaved ?? (explicitBuiltIn ? undefined : matchedSaved);
  const selectedView = activeSaved ? `saved:${activeSaved.name}` : explicitBuiltIn ? `builtin:${explicitBuiltIn.countKey}`
    : activeBuiltIn >= 0 ? `builtin:${BUILTINS[activeBuiltIn].countKey}` : "";

  return (
    <div className="xw-views xw-compact-views">
      <select aria-label="Saved views" value={selectedView} onChange={(e) => {
        const value = e.target.value;
        const view = value.startsWith("builtin:") ? BUILTINS.find((b) => b.countKey === value.slice(8))
          : views.find((v) => v.name === value.slice(6));
        if (view) { setChosenView(value); onApply(view.params); }
      }}>
        <option value="">Current filters</option>
        <optgroup label="Quick views">
          {BUILTINS.map((b) => <option key={b.name} value={`builtin:${b.countKey}`}>
            {b.name}{counts ? ` (${counts[b.countKey]})` : ""}
          </option>)}
        </optgroup>
        {views.length > 0 && <optgroup label="Your saved views">
          {views.map((v) => <option key={v.name} value={`saved:${v.name}`}>{v.name}</option>)}
        </optgroup>}
      </select>
      {!saving && <button className="small ghost" onClick={() => setSaving(true)}>Save view</button>}
      {activeSaved && <button className="small ghost" aria-label={`Delete saved view ${activeSaved.name}`}
        onClick={() => void remove(activeSaved.name)}>Delete view</button>}
      {saving && <form className="xw-save-view-form" onSubmit={(e) => { e.preventDefault(); void saveCurrent(); }}>
        <input aria-label="View name" placeholder="View name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        <button className="small" disabled={!name.trim()} type="submit">Save</button>
        <button className="small ghost" type="button" onClick={() => { setSaving(false); setName(""); }}>Cancel</button>
      </form>}
    </div>
  );
}
