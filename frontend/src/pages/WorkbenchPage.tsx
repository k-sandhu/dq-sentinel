import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import { qk } from "../api/queryKeys";
import type {
  Connection,
  Dataset,
  QueryRunResult,
  SavedQuery,
  SchemaTable,
  SuggestResult,
  VizType,
} from "../api/types";
import { canEdit, useAuth } from "../auth";
import { useConfirm } from "../components/confirm";
import PanelChart from "../components/PanelChart";
import { HistoryModal } from "../components/workbench/HistoryModal";
import ResultGrid from "../components/workbench/ResultGrid";
import { SaveQueryModal } from "../components/workbench/SaveQueryModal";
import { SavedQueriesRail } from "../components/workbench/SavedQueriesRail";
import { SchemaSidebar } from "../components/workbench/SchemaSidebar";
import {
  LIMITS,
  type TabState,
  canManageQuery,
  copyText,
  makeTab,
  nextLimitAfter,
} from "../components/workbench/shared";
import SqlEditor from "../components/workbench/SqlEditor";
import { Breadcrumbs, EmptyState, ErrorBox, Icon, Modal, Spinner } from "../components/ui";
import { downloadText, rowsToCsv, rowsToJson, rowsToTsv } from "../lib/csv";
import { fmtNum } from "../lib/format";
import { addHistory, clearHistory, loadHistory } from "../lib/queryHistory";
import type { QueryHistoryEntry } from "../lib/queryHistory";
import { formatSql } from "../lib/sqlFormat";
import { deriveTabTitle, loadTabsState, newTabId, persistTabsState } from "../lib/workbenchTabs";
import type { WorkbenchTab } from "../lib/workbenchTabs";

/** Body of PATCH /queries/{id} — only the fields SavedQueryUpdate accepts. */
interface SavedQueryPatch {
  name?: string;
  description?: string;
  sql?: string;
  tags?: string[];
  dataset_id?: number;
  unpin?: boolean;
}

const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((v, i) => v === b[i]);

/** Keep the toolbar chip a chip — the full name is always in the title attribute. */
const shortName = (name: string) => (name.length > 24 ? `${name.slice(0, 23)}…` : name);

/** Edit an entry in the shared saved-query library (#289): rename, re-describe,
 *  re-tag, move/clear the dataset pin, and optionally publish the editor's current
 *  SQL over the stored SQL. PATCH /queries/{id} was wired but had no UI, so the
 *  library was append-only — a typo in a name could only be fixed by delete +
 *  re-save, which lost the entry's history and its deep link. Uses the shared
 *  Modal + confirm dialog; deliberately not window.prompt (#213). */
function EditSavedQueryModal({
  query,
  editorSql,
  onClose,
  onSaved,
}: {
  query: SavedQuery;
  editorSql: string;
  onClose: () => void;
  onSaved: (q: SavedQuery) => void;
}) {
  const confirm = useConfirm();
  const fieldId = useId();
  const [name, setName] = useState(query.name);
  const [description, setDescription] = useState(query.description);
  const [tags, setTags] = useState(query.tags.join(", "));
  const [datasetId, setDatasetId] = useState<number | "">(query.dataset_id ?? "");
  const [replaceSql, setReplaceSql] = useState(false);

  const { data: datasets } = useQuery({
    queryKey: qk.datasets.byConnection(query.connection_id),
    queryFn: () => api.get<Dataset[]>(`/datasets?connection_id=${query.connection_id}`),
  });

  const trimmedName = name.trim();
  const trimmedDescription = description.trim();
  const nextTags = tags.split(",").map((t) => t.trim()).filter(Boolean);
  const sqlChanged = !!editorSql.trim() && editorSql.trim() !== query.sql.trim();
  const pinChanged = datasetId === "" ? query.dataset_id !== null : datasetId !== query.dataset_id;
  const dirty =
    trimmedName !== query.name ||
    trimmedDescription !== query.description ||
    !sameList(nextTags, query.tags) ||
    pinChanged ||
    (replaceSql && sqlChanged);

  const save = useMutation({
    mutationFn: () => {
      const body: SavedQueryPatch = {
        name: trimmedName,
        description: trimmedDescription,
        tags: nextTags,
      };
      // `dataset_id: null` means "leave the pin alone" server-side — clearing it
      // needs the explicit unpin flag.
      if (datasetId === "") {
        if (query.dataset_id !== null) body.unpin = true;
      } else {
        body.dataset_id = datasetId;
      }
      if (replaceSql && sqlChanged) body.sql = editorSql;
      return api.patch<SavedQuery>(`/queries/${query.id}`, body);
    },
    onSuccess: onSaved,
  });

  // Backdrop / Escape / ✕ all land here, so typed edits are never dropped silently.
  const requestClose = async () => {
    if (
      dirty &&
      !(await confirm({
        title: "Discard changes?",
        body: `Your edits to “${query.name}” haven't been saved.`,
        confirmLabel: "Discard",
        cancelLabel: "Keep editing",
        danger: true,
      }))
    )
      return;
    onClose();
  };

  return (
    <Modal
      title="Edit saved query"
      onClose={() => void requestClose()}
      footer={
        <>
          <button className="ghost" onClick={() => void requestClose()}>Cancel</button>
          <button
            className="primary"
            disabled={!trimmedName || !dirty || save.isPending}
            title={!trimmedName ? "Name can't be empty" : !dirty ? "No changes to save" : undefined}
            onClick={() => save.mutate()}
          >
            {save.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : null}
            Save changes
          </button>
        </>
      }
    >
      <label htmlFor={`${fieldId}-name`}>Name</label>
      <input
        id={`${fieldId}-name`}
        type="text"
        value={name}
        autoFocus
        aria-invalid={!trimmedName}
        aria-describedby={trimmedName ? undefined : `${fieldId}-name-err`}
        onChange={(e) => setName(e.target.value)}
      />
      {!trimmedName && (
        <div id={`${fieldId}-name-err`} className="field-error">Name can't be empty.</div>
      )}
      <label htmlFor={`${fieldId}-desc`} style={{ marginTop: 12 }}>Description</label>
      <input
        id={`${fieldId}-desc`}
        type="text"
        value={description}
        placeholder="What this query answers (optional)"
        onChange={(e) => setDescription(e.target.value)}
      />
      <label htmlFor={`${fieldId}-tags`} style={{ marginTop: 12 }}>Tags</label>
      <input
        id={`${fieldId}-tags`}
        type="text"
        value={tags}
        placeholder="comma-separated, e.g. triage, revenue"
        onChange={(e) => setTags(e.target.value)}
      />
      <label htmlFor={`${fieldId}-pin`} style={{ marginTop: 12 }}>Pin to dataset</label>
      <select
        id={`${fieldId}-pin`}
        value={datasetId}
        onChange={(e) => setDatasetId(e.target.value === "" ? "" : Number(e.target.value))}
      >
        <option value="">No pin</option>
        {(datasets ?? []).map((d) => (
          <option key={d.id} value={d.id}>{d.table_name}</option>
        ))}
      </select>
      <div style={{ fontSize: 11.5, color: "var(--text-light)", marginTop: 6 }}>
        Pinned queries appear on the dataset's Code tab as investigation starting points.
      </div>
      {sqlChanged && (
        <label
          className="field"
          style={{ marginTop: 12, display: "flex", gap: 8, alignItems: "flex-start", fontWeight: 400 }}
        >
          <input
            type="checkbox"
            checked={replaceSql}
            onChange={(e) => setReplaceSql(e.target.checked)}
            style={{ marginTop: 2, width: "auto" }}
          />
          <span>
            Replace the saved SQL with this tab's current SQL
            <span className="field-hint">
              Leave unchecked to edit only the name, description, tags and pin. Replacement SQL is
              re-validated as a single read-only SELECT.
            </span>
          </span>
        </label>
      )}
      <pre className="result" style={{ marginTop: 12, maxHeight: 130, fontSize: 11 }}>
        {replaceSql && sqlChanged ? editorSql : query.sql}
      </pre>
      <ErrorBox error={save.error} />
    </Modal>
  );
}

export default function WorkbenchPage() {
  const { user } = useAuth();
  const editable = canEdit(user);
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [params] = useSearchParams();
  const datasetId = params.get("dataset_id") ? Number(params.get("dataset_id")) : undefined;
  const runId = params.get("run_id") ? Number(params.get("run_id")) : undefined;
  const exceptionId = params.get("exception_id") ? Number(params.get("exception_id")) : undefined;
  const checkId = params.get("check_id") ? Number(params.get("check_id")) : undefined;
  const savedQueryId = params.get("saved_query_id") ? Number(params.get("saved_query_id")) : undefined;

  const [connectionId, setConnectionId] = useState<number | null>(
    params.get("connection_id") ? Number(params.get("connection_id")) : null,
  );
  const [limit, setLimit] = useState(200);
  const [showSave, setShowSave] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showSuggest, setShowSuggest] = useState(false);
  const suggestAutoOpened = useRef(false);
  const [history, setHistory] = useState<QueryHistoryEntry[]>(() => loadHistory());

  // Tabs (restored from localStorage; results stay in memory).
  const [{ initialTabs, initialActiveId }] = useState(() => {
    const saved = loadTabsState();
    const tabs = (saved?.tabs ?? [{ id: newTabId(), title: "Query 1", sql: "" }]).map((t) =>
      ({ ...makeTab(t.sql), id: t.id }),
    );
    const activeId = saved && tabs.some((t) => t.id === saved.activeId) ? saved.activeId : tabs[0].id;
    return { initialTabs: tabs, initialActiveId: activeId };
  });
  const [tabs, setTabs] = useState<TabState[]>(initialTabs);
  const [activeId, setActiveId] = useState<string>(initialActiveId);
  const active = tabs.find((t) => t.id === activeId) ?? tabs[0];

  // Which library entry (if any) seeded each tab. Keeps the "edit this saved
  // query" affordance attached to the tab it belongs to rather than to the page,
  // so switching tabs can't retarget an edit at the wrong entry (#289).
  const [tabSaved, setTabSaved] = useState<Record<string, SavedQuery>>({});
  const linkSaved = (tabId: string, q: SavedQuery | null) =>
    setTabSaved((prev) => {
      if (q) return { ...prev, [tabId]: q };
      if (!(tabId in prev)) return prev;
      const next = { ...prev };
      delete next[tabId];
      return next;
    });
  const activeSaved = tabSaved[activeId] ?? null;
  const canEditSaved = !!activeSaved && canManageQuery(user, activeSaved);
  // The edit dialog belongs to one tab; switching tabs must not re-open it later
  // pointed at whatever entry the new tab happens to hold.
  useEffect(() => setShowEdit(false), [activeId]);

  // Persist id/title/sql for the last session (titles re-derived from SQL).
  useEffect(() => {
    const persisted: WorkbenchTab[] = tabs.map((t, i) => ({ id: t.id, title: deriveTabTitle(t.sql, i), sql: t.sql }));
    persistTabsState({ tabs: persisted, activeId });
  }, [tabs, activeId]);

  const patchTab = (id: string, patch: Partial<TabState> | ((t: TabState) => Partial<TabState>)) =>
    setTabs((prev) => prev.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)));
  const patchActive = (patch: Partial<TabState> | ((t: TabState) => Partial<TabState>)) => patchTab(activeId, patch);
  const editActiveSql = (next: string) => patchActive({ sql: next, dirty: true });

  const { data: connections } = useQuery({
    queryKey: qk.connections.list(),
    queryFn: () => api.get<Connection[]>("/connections"),
  });
  const { data: dataset } = useQuery({
    queryKey: qk.datasets.detail(datasetId!),
    queryFn: () => api.get<Dataset>(`/datasets/${datasetId}`),
    enabled: !!datasetId,
  });
  const schemaQuery = useQuery({
    queryKey: qk.schema.detail(connectionId),
    queryFn: () => api.get<SchemaTable[]>(`/connections/${connectionId}/schema`),
    enabled: !!connectionId,
    staleTime: 120_000,
  });
  const tables = useMemo(() => schemaQuery.data ?? [], [schemaQuery.data]);

  useEffect(() => {
    if (!connectionId && dataset) setConnectionId(dataset.connection_id);
    else if (!connectionId && !datasetId && connections?.length) setConnectionId(connections[0].id);
  }, [dataset, connections, connectionId, datasetId]);

  // Deep-link: surface a saved query's SQL (and its connection) on arrival. Applied
  // once — reuse the active tab when it's empty, else open a dedicated tab so we
  // never clobber work restored from a previous session (tabs persist to localStorage).
  const deepLinked = useQuery({
    queryKey: qk.savedQuery.detail(savedQueryId!),
    queryFn: () => api.get<SavedQuery>(`/queries/${savedQueryId}`),
    enabled: !!savedQueryId,
  });
  const deepLinkApplied = useRef(false);
  useEffect(() => {
    if (!deepLinked.data || deepLinkApplied.current) return;
    deepLinkApplied.current = true;
    const { sql, connection_id } = deepLinked.data;
    setConnectionId(connection_id);
    if (!active.dirty && !active.sql.trim()) {
      patchActive({ sql, result: null, error: null });
      linkSaved(activeId, deepLinked.data);
    } else {
      const t = makeTab(sql);
      setTabs((prev) => [...prev, t]);
      setActiveId(t.id);
      linkSaved(t.id, deepLinked.data);
    }
    // React only to the fetched query landing.
  }, [deepLinked.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const suggest = useQuery({
    queryKey: qk.suggest.detail({ connectionId, datasetId, runId, exceptionId, checkId }),
    queryFn: () =>
      api.post<SuggestResult>("/query/suggest", {
        connection_id: connectionId,
        dataset_id: datasetId,
        run_id: runId,
        exception_id: exceptionId,
        check_id: checkId,
      }),
    enabled: !!connectionId || !!datasetId || !!runId || !!exceptionId || !!checkId,
    staleTime: 60_000,
  });
  const suggestCount = suggest.data?.suggestions.length ?? 0;

  // Open the suggestions rail on its own the first time real suggestions arrive,
  // so it never sits empty taking space — the user can re-toggle it from the header.
  useEffect(() => {
    if (!suggestAutoOpened.current && suggestCount > 0) {
      suggestAutoOpened.current = true;
      setShowSuggest(true);
    }
  }, [suggestCount]);

  const recordHistory = (sqlText: string, connId: number, r: QueryRunResult | null, err: unknown) => {
    const conn = connections?.find((c) => c.id === connId);
    addHistory({
      connectionId: connId,
      connectionName: conn?.name ?? "",
      sql: sqlText,
      rowCount: r ? r.row_count : null,
      elapsedMs: r ? r.elapsed_ms : null,
      ok: !!r,
      error: r ? null : err instanceof Error ? err.message : err ? String(err) : "failed",
    });
    setHistory(loadHistory());
  };

  const run = useMutation({
    mutationFn: (vars: { tabId: string; sql: string; connectionId: number; limit: number }) =>
      api.post<QueryRunResult>("/query/run", { connection_id: vars.connectionId, sql: vars.sql, limit: vars.limit }),
    onSuccess: (r, vars) => {
      recordHistory(vars.sql, vars.connectionId, r, null);
      // run.reset() can't cancel an in-flight request, so a slow run could land
      // after the source was switched — drop a result whose connection is stale.
      if (vars.connectionId !== connectionId) return;
      patchTab(vars.tabId, {
        result: r,
        error: null,
        resultLimit: vars.limit,
        view: "table",
        chart: { type: "bar", x: r.columns[0] ?? "", y: r.columns[r.columns.length - 1] ?? "" },
      });
    },
    onError: (err, vars) => {
      recordHistory(vars.sql, vars.connectionId, null, err);
      if (vars.connectionId !== connectionId) return;
      patchTab(vars.tabId, { result: null, error: err instanceof Error ? err.message : String(err) });
    },
  });

  // Single funnel for every run path: gate on role/connection, clear the tab's
  // prior error, and capture the connection used so a stale result can be dropped.
  const runSql = (tabId: string, sql: string, lim: number = limit) => {
    if (!editable || !connectionId || !sql.trim()) return;
    patchTab(tabId, { error: null });
    run.mutate({ tabId, sql, connectionId, limit: lim });
  };
  const runActive = () => runSql(activeId, active.sql);

  const nextLimit = nextLimitAfter(active.result ? active.resultLimit : limit);
  const raiseLimit = () => {
    setLimit(nextLimit);
    runSql(activeId, active.sql, nextLimit);
  };

  const dialect = useMemo(
    () => connections?.find((c) => c.id === connectionId)?.kind ?? null,
    [connections, connectionId],
  );

  // Insert a schema-browser identifier into the active editor. Table references
  // seed an empty editor with a schema-qualified `SELECT * … LIMIT 50`; columns
  // (and tables added to existing SQL) are appended verbatim — the caller already
  // quoted/qualified the text for the active dialect (#83).
  const insert = (text: string, opts?: { table?: boolean }) =>
    patchActive((t) => ({
      sql: t.sql.trim() ? `${t.sql.trimEnd()} ${text}` : opts?.table ? `SELECT * FROM ${text} LIMIT 50` : text,
      dirty: true,
    }));

  const confirmReplace = async () =>
    !active.dirty ||
    !active.sql.trim() ||
    (await confirm({
      title: "Replace the current query?",
      body: "The unsaved SQL in this tab will be discarded.",
      confirmLabel: "Replace query",
      cancelLabel: "Keep editing",
      danger: true,
    }));

  // Load SQL into the active tab from a saved query / history entry / suggestion,
  // switching the source if needed. Every path that REPLACES the editor's contents
  // must come through here so a dirty tab is never clobbered silently (#284).
  // Switching connections clears every tab's stale result. `saved` records which
  // library entry the tab now shows (null for history/suggestions), which is what
  // the "Edit saved query" affordance targets.
  const loadSql = async (
    sql: string,
    sourceConnectionId: number,
    thenRun: boolean,
    saved: SavedQuery | null = null,
  ) => {
    if (!(await confirmReplace())) return;
    const sameConn = sourceConnectionId === connectionId;
    if (!sameConn && sourceConnectionId) {
      setConnectionId(sourceConnectionId);
      setTabs((prev) => prev.map((t) => ({ ...t, result: null, error: null, view: "table" })));
    }
    patchActive({ sql, dirty: false, result: null, error: null });
    linkSaved(activeId, saved);
    if (thenRun && sameConn) runSql(activeId, sql);
  };

  // Switching the source must not leave results pointed at the old database.
  const changeConnection = (id: number) => {
    if (id === connectionId) return;
    setConnectionId(id);
    setTabs((prev) => prev.map((t) => ({ ...t, result: null, view: "table" })));
    run.reset();
  };

  const addTab = () => {
    const t = makeTab();
    setTabs((prev) => [...prev, t]);
    setActiveId(t.id);
  };
  const closeTab = (id: string) => {
    const idx = tabs.findIndex((t) => t.id === id);
    if (idx === -1) return;
    linkSaved(id, null); // don't leak the closed tab's saved-query link
    const fresh = tabs.length <= 1 ? makeTab() : null;
    // Functional update so the removal never operates on a stale array.
    setTabs((prev) => {
      const remaining = prev.filter((t) => t.id !== id);
      return remaining.length ? remaining : [fresh ?? makeTab()];
    });
    if (activeId === id) {
      const remaining = tabs.filter((t) => t.id !== id);
      setActiveId(remaining.length ? remaining[Math.min(idx, remaining.length - 1)].id : fresh!.id);
    }
  };

  const numericColumns = useMemo(() => {
    const r = active.result;
    if (!r) return [];
    return r.columns.filter((_c, i) => r.rows.some((row) => typeof row[i] === "number"));
  }, [active.result]);
  const setChartPatch = (patch: Partial<TabState["chart"]>) => patchActive((t) => ({ chart: { ...t.chart, ...patch } }));

  return (
    <div className="page" style={{ maxWidth: 1500 }}>
      {dataset && (
        <Breadcrumbs
          items={[
            { label: "Datasets", to: "/datasets" },
            { label: dataset.table_name, to: `/datasets/${dataset.id}` },
            { label: "Workbench" },
          ]}
        />
      )}
      <div className="page-header">
        <div>
          <h1>Workbench</h1>
          <div className="sub">
            Run read-only SQL against your sources
            {dataset ? <> · context: <strong>{dataset.table_name}</strong></> : null}
            {!editable && <span className="badge" style={{ marginLeft: 8 }}>viewer — running queries requires editor</span>}
          </div>
        </div>
        <div className="header-actions">
          <button
            className={`small wb-toggle${showSuggest ? " on" : ""}`}
            onClick={() => setShowSuggest((v) => !v)}
            title="Toggle suggested queries"
            aria-pressed={showSuggest}
          >
            <Icon name="bolt" size={12} /> Suggestions{suggestCount ? ` (${suggestCount})` : ""}
          </button>
          <select
            value={connectionId ?? ""}
            onChange={(e) => changeConnection(Number(e.target.value))}
            aria-label="Connection"
            style={{ marginTop: 0, width: 220 }}
          >
            {connections?.map((c) => (
              <option key={c.id} value={c.id}>{c.name} ({c.kind})</option>
            ))}
          </select>
        </div>
      </div>

      <div className={`wb-grid${showSuggest ? " with-suggest" : ""}`}>
        <div>
          <div className="card card-pad">
            <h3>Schema</h3>
            {connectionId ? (
              <SchemaSidebar connectionId={connectionId} dialect={dialect} onInsert={insert} />
            ) : (
              <div className="empty">Pick a connection</div>
            )}
          </div>
          {connectionId && (
            <SavedQueriesRail
              connectionId={connectionId}
              editable={editable}
              onLoad={(q) => void loadSql(q.sql, q.connection_id, false, q)}
              onRun={(q) => void loadSql(q.sql, q.connection_id, true, q)}
            />
          )}
        </div>

        <div>
          <div className="wb-tabs">
            {tabs.map((t, i) => (
              <div
                key={t.id}
                className={`wb-tab${t.id === activeId ? " active" : ""}`}
                onClick={() => setActiveId(t.id)}
                title={t.sql || "Empty query"}
              >
                <span className="wb-tab-name">{deriveTabTitle(t.sql, i)}{t.dirty ? " •" : ""}</span>
                {tabs.length > 1 && (
                  <span
                    className="wb-tab-close"
                    role="button"
                    aria-label="Close tab"
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(t.id);
                    }}
                  >
                    <Icon name="x" size={11} />
                  </span>
                )}
              </div>
            ))}
            <button className="wb-tab-add" title="New query tab" onClick={addTab}>
              <Icon name="plus" size={12} />
            </button>
          </div>

          <div className="card card-pad" style={{ marginBottom: 14 }}>
            <SqlEditor
              key={active.id}
              value={active.sql}
              onChange={editActiveSql}
              onRun={runActive}
              tables={tables}
              dialect={dialect}
              readOnly={!editable}
              placeholder={"SELECT status, COUNT(*) AS n\nFROM orders\nGROUP BY 1\nORDER BY n DESC"}
            />
            <div className="toolbar" style={{ marginBottom: 0, marginTop: 10 }}>
              <button
                className="primary"
                disabled={!editable || !active.sql.trim() || !connectionId || run.isPending}
                onClick={runActive}
                title="Ctrl/Cmd+Enter"
              >
                {run.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="play" size={13} />}
                Run
              </button>
              <button
                className="small"
                disabled={!active.sql.trim()}
                onClick={() => editActiveSql(formatSql(active.sql, dialect))}
                title="Format SQL"
              >
                Format
              </button>
              <button
                className="small"
                disabled={!editable || !active.sql.trim() || !connectionId}
                onClick={() => setShowSave(true)}
                title="Save this query to the shared team library"
              >
                <Icon name="plus" size={12} /> Save
              </button>
              {/* Only offered once this tab is tied to a library entry — renaming
                  is an edit of that entry, not of the editor's contents (#289).
                  Non-owners get the reason as visible text rather than a dead
                  disabled control, matching the rail's creator/admin gate. */}
              {activeSaved && canEditSaved && (
                <button
                  className="small"
                  aria-label={`Edit saved query ${activeSaved.name}`}
                  title={`Rename, re-describe, re-tag or re-pin “${activeSaved.name}”`}
                  onClick={() => setShowEdit(true)}
                >
                  <Icon name="settings" size={12} /> Edit “{shortName(activeSaved.name)}”
                </button>
              )}
              {activeSaved && !canEditSaved && (
                <span className="badge" title={`Saved query “${activeSaved.name}”`}>
                  “{shortName(activeSaved.name)}” · only {activeSaved.created_by ?? "its creator"} or an
                  admin can edit
                </span>
              )}
              <button className="small" onClick={() => setShowHistory(true)} title="Recent queries (this browser)">
                <Icon name="refresh" size={12} /> History{history.length ? ` (${history.length})` : ""}
              </button>
              <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} style={{ marginTop: 0, width: 120 }}>
                {LIMITS.map((n) => (
                  <option key={n} value={n}>limit {n}</option>
                ))}
              </select>
              <span style={{ fontSize: 11.5, color: "var(--text-light)" }}>
                Read-only · single SELECT/WITH · <span className="kbd">Ctrl</span>+<span className="kbd">Enter</span> to run
              </span>
            </div>
            <ErrorBox error={active.error} />
          </div>

          {active.result ? (
            <div className="card">
              <div className="card-pad" style={{ display: "flex", gap: 12, alignItems: "center", paddingBottom: 10, flexWrap: "wrap" }}>
                <strong>{fmtNum(active.result.row_count)} rows</strong>
                <span style={{ color: "var(--text-light)", fontSize: 12 }}>{active.result.elapsed_ms} ms</span>
                {active.result.truncated && (active.resultLimit < 2000 ? (
                  <button className="badge" style={{ cursor: "pointer" }} onClick={raiseLimit} disabled={run.isPending} title="Fetch more rows">
                    truncated · raise to {nextLimit} &amp; re-run
                  </button>
                ) : (
                  <span className="badge" title="Maximum result size">truncated at 2000 (max)</span>
                ))}
                <div className="right" style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  {active.view === "table" && (
                    <>
                      <button className="ghost small" onClick={() => patchActive((t) => ({ showFilters: !t.showFilters }))} title="Toggle per-column filters">
                        <Icon name="search" size={12} /> Filter
                      </button>
                      <button
                        className="ghost small"
                        onClick={() => active.result && copyText(rowsToTsv(active.result.columns, active.result.rows))}
                        title="Copy all rows (TSV)"
                      >
                        <Icon name="copy" size={12} /> Copy
                      </button>
                      <button
                        className="ghost small"
                        onClick={() => active.result && downloadText("query-result.csv", rowsToCsv(active.result.columns, active.result.rows), "text/csv")}
                        title="Export CSV"
                      >
                        CSV
                      </button>
                      <button
                        className="ghost small"
                        onClick={() => active.result && downloadText("query-result.json", rowsToJson(active.result.columns, active.result.rows), "application/json")}
                        title="Export JSON"
                      >
                        JSON
                      </button>
                    </>
                  )}
                  {numericColumns.length > 0 && active.result.columns.length >= 2 && (
                    <button className="small" onClick={() => patchActive((t) => ({ view: t.view === "chart" ? "table" : "chart" }))}>
                      {active.view === "chart" ? "Table" : "Chart"}
                    </button>
                  )}
                </div>
              </div>
              {active.view === "chart" ? (
                <div className="card-pad" style={{ paddingTop: 0 }}>
                  <div className="toolbar">
                    <select value={active.chart.type} onChange={(e) => setChartPatch({ type: e.target.value as VizType })} style={{ marginTop: 0, width: 100 }}>
                      {["bar", "line", "area", "pie"].map((t) => <option key={t}>{t}</option>)}
                    </select>
                    <select value={active.chart.x} onChange={(e) => setChartPatch({ x: e.target.value })} style={{ marginTop: 0, width: 150 }}>
                      {active.result.columns.map((c, i) => <option key={`${c}-${i}`}>{c}</option>)}
                    </select>
                    <select value={active.chart.y} onChange={(e) => setChartPatch({ y: e.target.value })} style={{ marginTop: 0, width: 150 }}>
                      {numericColumns.map((c, i) => <option key={`${c}-${i}`}>{c}</option>)}
                    </select>
                  </div>
                  <PanelChart
                    columns={active.result.columns}
                    rows={active.result.rows}
                    viz={{ type: active.chart.type, x: active.chart.x, y: active.chart.y }}
                    height={300}
                  />
                </div>
              ) : (
                <div className="card-pad" style={{ paddingTop: 0 }}>
                  <ResultGrid result={active.result} showFilters={active.showFilters} />
                </div>
              )}
            </div>
          ) : run.isPending ? (
            <div className="card card-pad"><Spinner label="Running…" /></div>
          ) : (
            <div className="card">
              <EmptyState title="Results appear here" hint="Write SQL, click a suggestion, or insert a table from the schema browser." />
            </div>
          )}
        </div>

        {showSuggest && (
        <div className="card card-pad">
          <div className="wb-suggest-head">
            <h3>
              Suggested queries{" "}
              {suggest.data && (
                <span className={`badge ${suggest.data.mode === "llm" ? "ai" : ""}`}>
                  {suggest.data.mode === "llm" ? "AI" : "heuristic"}
                </span>
              )}
            </h3>
            <button className="ghost small" onClick={() => setShowSuggest(false)} title="Hide suggestions" aria-label="Hide suggestions">
              <Icon name="x" size={12} />
            </button>
          </div>
          {suggest.isLoading && <Spinner label="Thinking…" />}
          {(suggest.data?.suggestions ?? []).map((s, i) => (
            <div key={i} className="insight" style={{ borderColor: "var(--purple)" }}>
              <div className="t">{s.title}</div>
              <div style={{ fontSize: 11.5, color: "var(--text-light)", margin: "2px 0 6px" }}>{s.rationale}</div>
              <pre className="result" style={{ maxHeight: 110, fontSize: 11 }}>{s.sql}</pre>
              {/* Suggestions replace the active tab's SQL, so they go through the
                  same loadSql/confirmReplace funnel as the saved-query rail and
                  history — they used to overwrite a dirty tab silently (#284).
                  Suggestions are always generated for the current source. */}
              <div style={{ display: "flex", gap: 6 }}>
                {editable && (
                  <button className="primary small" onClick={() => void loadSql(s.sql, connectionId ?? 0, true)}>
                    Run
                  </button>
                )}
                <button className="small" onClick={() => void loadSql(s.sql, connectionId ?? 0, false)}>Edit</button>
              </div>
            </div>
          ))}
          {suggest.data && suggest.data.suggestions.length === 0 && (
            <div className="empty" style={{ padding: 14 }}>No suggestions for this context.</div>
          )}
        </div>
        )}
      </div>

      {showSave && connectionId && (
        <SaveQueryModal
          connectionId={connectionId}
          sql={active.sql}
          defaultDatasetId={dataset?.connection_id === connectionId ? datasetId : undefined}
          onClose={() => setShowSave(false)}
          onSaved={(q) => {
            setShowSave(false);
            patchActive({ dirty: false });
            linkSaved(activeId, q); // the tab now IS this library entry — editable at once
            qc.invalidateQueries({ queryKey: qk.savedQueries.all });
          }}
        />
      )}

      {showEdit && activeSaved && (
        <EditSavedQueryModal
          query={activeSaved}
          editorSql={active.sql}
          onClose={() => setShowEdit(false)}
          onSaved={(q) => {
            setShowEdit(false);
            linkSaved(activeId, q);
            // If the stored SQL was republished, the tab is in sync with the library.
            if (q.sql.trim() === active.sql.trim()) patchActive({ dirty: false });
            qc.invalidateQueries({ queryKey: qk.savedQueries.all });
            qc.invalidateQueries({ queryKey: qk.savedQuery.detail(q.id) });
          }}
        />
      )}

      {showHistory && (
        <HistoryModal
          history={history}
          editable={editable}
          onClose={() => setShowHistory(false)}
          onLoad={(entry, thenRun) => {
            setShowHistory(false);
            void loadSql(entry.sql, entry.connectionId, thenRun);
          }}
          onClear={() => setHistory(clearHistory())}
        />
      )}
    </div>
  );
}
