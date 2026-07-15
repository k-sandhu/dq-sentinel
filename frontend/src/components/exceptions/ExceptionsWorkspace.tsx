// State orchestration for the exceptions triage workspace (#63):
// URL params <-> filters, layout grid, the central triage mutation, the
// keyboard handler, and detail-panel focus management.
//
// Embedded usage (dataset Exceptions tab, run drill-ins) passes pinned filters
// via props; those are added to every request and never appear as user controls.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Assignee, ExceptionFacets, ExceptionPage, ExceptionRecord } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import BulkBar from "./BulkBar";
import DetailPanel from "./DetailPanel";
import ExceptionTable from "./ExceptionTable";
import FilterBar from "./FilterBar";
import SavedViews from "./SavedViews";
import ShortcutsHelp from "./ShortcutsHelp";
import { PAGE_SIZE, SELECTION_CAP, parseFilters, toApiParams } from "./shared";
import type { WorkspaceFilters } from "./shared";

interface TriagePayload {
  status?: string;
  note?: string;
  assigned_to_id?: number | null;
  clear_assignee?: boolean;
}

export default function ExceptionsWorkspace({
  datasetId,
  runId,
  checkId,
}: {
  datasetId?: number;
  runId?: number;
  checkId?: number;
}) {
  const { user } = useAuth();
  const editable = canEdit(user);
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const filters = useMemo(() => parseFilters(sp), [sp]);

  // Selection survives paging (store ids); focus + toast are local UI state.
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [focusedId, setFocusedId] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const rowRefs = useRef<Map<number, HTMLTableRowElement>>(new Map());
  const lastFocusedRow = useRef<HTMLElement | null>(null);

  const pinned = { datasetId, runId, checkId };
  const apiParams = useMemo(() => toApiParams(filters, pinned).toString(), [filters, datasetId, runId, checkId]);
  const listParams = useMemo(() => {
    const p = new URLSearchParams(apiParams);
    p.set("limit", String(PAGE_SIZE));
    p.set("offset", String(filters.offset));
    if (filters.sort && filters.sort !== "newest") p.set("sort", filters.sort);
    return p.toString();
  }, [apiParams, filters.offset, filters.sort]);

  const list = useQuery({
    queryKey: qk.exceptions.list(listParams),
    queryFn: () => api.get<ExceptionPage>(`/exceptions?${listParams}`),
    refetchInterval: 30_000, // teammates triage the same queue (#63 concurrency)
    placeholderData: keepPreviousData, // keep rows on filter change — no flicker
  });

  const facetsQ = useQuery({
    queryKey: qk.exceptionsFacets.list(apiParams),
    queryFn: () => api.get<ExceptionFacets>(`/exceptions/facets?${apiParams}`),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });

  const { data: assignees = [] } = useQuery({
    queryKey: qk.assignees.list(),
    queryFn: () => api.get<Assignee[]>("/auth/assignees"),
    staleTime: 5 * 60_000,
  });

  const items = useMemo(() => list.data?.items ?? [], [list.data]);
  const total = list.data?.total ?? 0;
  const openExc = filters.sel != null ? items.find((e) => e.id === filters.sel) ?? null : null;

  // ----- URL helpers -----
  const update = useCallback(
    (patch: Partial<WorkspaceFilters>) => {
      const next = new URLSearchParams(sp);
      const setMulti = (key: string, vals: string[]) => {
        next.delete(key);
        for (const v of vals) next.append(key, v);
      };
      for (const [k, v] of Object.entries(patch)) {
        if (k === "status" || k === "severity") setMulti(k, v as string[]);
        else if (k === "sel") {
          if (v == null) next.delete("sel");
          else next.set("sel", String(v));
        } else if (v === "" || v == null || (k === "offset" && v === 0) || (k === "sort" && v === "newest") || (k === "group" && v === "none")) {
          next.delete(k);
        } else {
          next.set(k, String(v));
        }
      }
      setSp(next, { replace: true });
    },
    [sp, setSp],
  );

  const applyViewParams = useCallback(
    (paramStr: string) => {
      // Replace all filter params with the saved view's set (keep nothing).
      setSelected(new Set());
      setSp(new URLSearchParams(paramStr), { replace: false });
    },
    [setSp],
  );

  const clearAll = useCallback(() => {
    setSelected(new Set());
    setSp(new URLSearchParams(), { replace: false });
  }, [setSp]);

  // ----- selection -----
  const toggleSelect = useCallback((id: number) => {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else if (next.size < SELECTION_CAP) next.add(id);
      return next;
    });
  }, []);

  const toggleGroup = useCallback((ids: number[]) => {
    setSelected((cur) => {
      const next = new Set(cur);
      const allIn = ids.every((id) => next.has(id));
      if (allIn) ids.forEach((id) => next.delete(id));
      else ids.forEach((id) => next.size < SELECTION_CAP && next.add(id));
      return next;
    });
  }, []);

  // ----- the one triage mutation (panel, bulk bar, and keyboard share it) -----
  const triage = useMutation({
    mutationFn: ({ ids, payload }: { ids: number[]; payload: TriagePayload }) =>
      api.post<ExceptionRecord[]>("/exceptions/triage", { ids, ...payload }),
    onSuccess: (returned, { ids }) => {
      // If fewer came back than requested, someone else already triaged some.
      if (returned.length < ids.length) {
        setToast(`${ids.length - returned.length} already triaged by someone else`);
      }
      qc.invalidateQueries({ queryKey: qk.exceptions.all });
      qc.invalidateQueries({ queryKey: qk.exceptionsFacets.all });
      qc.invalidateQueries({ queryKey: qk.exceptionViewCounts.all });
      qc.invalidateQueries({ queryKey: qk.dashboard.all });
      qc.invalidateQueries({ queryKey: qk.datasets.all });
    },
    onError: (e) => setToast(e instanceof Error ? e.message : "Triage failed"),
  });

  const doTriage = useCallback(
    (ids: number[], payload: TriagePayload) => {
      if (!editable || ids.length === 0) return;
      triage.mutate({ ids, payload });
    },
    [editable, triage],
  );

  // Bulk/keyboard apply to the current selection if non-empty, else focused row.
  const targetIds = useCallback((): number[] => {
    if (selected.size > 0) return [...selected];
    if (focusedId != null) return [focusedId];
    return [];
  }, [selected, focusedId]);

  const openPanel = useCallback(
    (exc: ExceptionRecord) => {
      lastFocusedRow.current = rowRefs.current.get(exc.id) ?? null;
      setFocusedId(exc.id);
      update({ sel: exc.id });
    },
    [update],
  );

  const closePanel = useCallback(() => update({ sel: null }), [update]);

  // Toast auto-dismiss.
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  // Keep a valid focused row as the page changes.
  useEffect(() => {
    if (items.length === 0) {
      setFocusedId(null);
    } else if (focusedId == null || !items.some((e) => e.id === focusedId)) {
      setFocusedId(items[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  // ----- keyboard handler (guarded exactly like Layout's "/" handler) -----
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // Never swallow modifier combos (Cmd+R etc.).
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;

      const idx = focusedId != null ? items.findIndex((x) => x.id === focusedId) : -1;
      const moveTo = (n: number) => {
        const clamped = Math.max(0, Math.min(items.length - 1, n));
        const next = items[clamped];
        if (!next) return;
        setFocusedId(next.id);
        rowRefs.current.get(next.id)?.scrollIntoView({ block: "nearest" });
        if (filters.sel != null) update({ sel: next.id }); // panel follows focus
      };

      switch (e.key) {
        case "j":
          e.preventDefault();
          moveTo(idx < 0 ? 0 : idx + 1);
          return;
        case "k":
          e.preventDefault();
          moveTo(idx < 0 ? 0 : idx - 1);
          return;
        case "x":
          if (editable && focusedId != null) {
            e.preventDefault();
            toggleSelect(focusedId);
          }
          return;
        case "o":
        case "Enter": {
          const cur = items[idx];
          if (cur) {
            e.preventDefault();
            openPanel(cur);
          }
          return;
        }
        case "Escape":
          if (filters.sel != null) closePanel();
          else if (selected.size > 0) setSelected(new Set());
          return;
        case "?":
          e.preventDefault();
          setHelpOpen((o) => !o);
          return;
      }

      if (!editable) return;
      const letterStatus: Record<string, string> = { a: "acknowledged", e: "expected", r: "resolved", m: "muted", u: "open" };
      if (e.key in letterStatus) {
        const ids = targetIds();
        if (ids.length) {
          e.preventDefault();
          doTriage(ids, { status: letterStatus[e.key] });
        }
        return;
      }
      if (e.key === "A" && e.shiftKey && user) {
        const ids = targetIds();
        if (ids.length) {
          e.preventDefault();
          doTriage(ids, { assigned_to_id: user.id });
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [items, focusedId, filters.sel, selected, editable, user, doTriage, toggleSelect, openPanel, closePanel, targetIds, update]);

  const panelOpen = openExc != null;

  return (
    <div className={`xw-root${panelOpen ? " xw-panel-open" : ""}`}>
      <div className="xw-main">
        <SavedViews currentParams={sp.toString()} onApply={applyViewParams} />
        <FilterBar
          filters={filters}
          facets={facetsQ.data}
          assignees={assignees}
          exportUrl={apiParams}
          update={update}
          clearAll={clearAll}
        />
        {(list.error || facetsQ.error) && (
          <div className="error-box">
            {(list.error as Error)?.message ?? (facetsQ.error as Error)?.message}
          </div>
        )}
        <ExceptionTable
          items={items}
          total={total}
          offset={filters.offset}
          pageSize={PAGE_SIZE}
          loading={list.isFetching}
          group={filters.group}
          selected={selected}
          focusedId={focusedId}
          editable={editable}
          hideDataset={!!datasetId}
          rowRefs={rowRefs}
          onToggleSelect={toggleSelect}
          onToggleGroup={toggleGroup}
          onOpen={openPanel}
          onPage={(off) => update({ offset: off })}
        />
        {editable && selected.size > 0 && (
          <BulkBar
            count={selected.size}
            assignees={assignees}
            triaging={triage.isPending}
            onTriage={(payload) => doTriage([...selected], payload)}
            onClear={() => setSelected(new Set())}
          />
        )}
      </div>

      {panelOpen && openExc && (
        <DetailPanel
          exc={openExc}
          assignees={assignees}
          onClose={closePanel}
          onTriage={doTriage}
          triaging={triage.isPending}
          returnFocusRef={lastFocusedRow}
        />
      )}

      {toast && (
        <div className="xw-toast" role="status">
          {toast}
        </div>
      )}
      {helpOpen && <ShortcutsHelp onClose={() => setHelpOpen(false)} />}
    </div>
  );
}
