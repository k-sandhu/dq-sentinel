import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import type {
  Connection,
  Dataset,
  Ddl,
  QueryRunResult,
  SavedQuery,
  SchemaTable,
  SuggestResult,
  User,
  VizType,
} from "../api/types";
import { canEdit, isAdmin, useAuth } from "../auth";
import PanelChart from "../components/PanelChart";
import ResultGrid from "../components/workbench/ResultGrid";
import SqlEditor from "../components/workbench/SqlEditor";
import { Breadcrumbs, EmptyState, ErrorBox, Icon, Modal, Spinner } from "../components/ui";
import { downloadText, rowsToCsv, rowsToJson, rowsToTsv } from "../lib/csv";
import { fmtNum, timeAgo } from "../lib/format";
import { addHistory, clearHistory, loadHistory } from "../lib/queryHistory";
import type { QueryHistoryEntry } from "../lib/queryHistory";
import { formatSql } from "../lib/sqlFormat";
import { qualifiedRef, quoteIdent } from "../lib/sqlIdent";
import { deriveTabTitle, loadTabsState, newTabId, persistTabsState } from "../lib/workbenchTabs";
import type { WorkbenchTab } from "../lib/workbenchTabs";

function SchemaSidebar({
  connectionId,
  dialect,
  onInsert,
}: {
  connectionId: number;
  dialect: string | null;
  onInsert: (text: string, opts?: { table?: boolean }) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [ddlTable, setDdlTable] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["schema", connectionId],
    queryFn: () => api.get<SchemaTable[]>(`/connections/${connectionId}/schema`),
    staleTime: 120_000,
  });
  const ddl = useQuery({
    queryKey: ["ddl", connectionId, ddlTable],
    queryFn: () => api.get<Ddl>(`/connections/${connectionId}/ddl?table=${encodeURIComponent(ddlTable!)}`),
    enabled: !!ddlTable,
  });

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (isLoading) return <Spinner label="Introspecting…" />;
  if (!data || data.length === 0)
    return <div className="empty" style={{ padding: 14, fontSize: 12.5 }}>No tables on this connection.</div>;

  return (
    <>
      <div className="schema-hint">Click a table to add it · ▸ to see columns</div>
      <div className="schema-tree">
        {data.map((t) => {
          const key = t.table_name;
          const expanded = open.has(key);
          return (
            <div key={key} className="schema-table">
              <div className="schema-row">
                <button
                  className="schema-caret"
                  onClick={() => toggle(key)}
                  aria-label={expanded ? "Collapse columns" : "Expand columns"}
                  aria-expanded={expanded}
                >
                  {expanded ? "▾" : "▸"}
                </button>
                <button
                  className="schema-name"
                  onClick={() => onInsert(qualifiedRef(t.schema_name, t.table_name, dialect), { table: true })}
                  title="Add this table to the query"
                >
                  <Icon name="table" size={12} />
                  <span className="nm">{t.table_name}</span>
                  {t.kind === "view" && <span className="badge kind">view</span>}
                </button>
                <button
                  className="schema-ddl"
                  title="View definition (DDL)"
                  aria-label={`View definition of ${t.table_name}`}
                  onClick={() => setDdlTable(t.table_name)}
                >
                  <Icon name="book" size={12} />
                </button>
              </div>
              {expanded && (
                <div className="schema-cols">
                  {t.columns.map((c) => (
                    <button
                      key={c.name}
                      className="schema-col"
                      onClick={() => onInsert(quoteIdent(c.name, dialect))}
                      title="Insert column"
                    >
                      <span className="cn">{c.name}</span>
                      <span className="ct">{c.dtype.toLowerCase().slice(0, 12)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {ddlTable && (
        <Modal title={`Definition of ${ddlTable}`} onClose={() => setDdlTable(null)} wide>
          {ddl.isLoading ? (
            <Spinner />
          ) : (
            <>
              <div style={{ marginBottom: 8 }}>
                <span className="badge">{ddl.data?.source === "database" ? "as stored in the database" : "synthesized from introspection"}</span>
              </div>
              <pre className="sql">{ddl.data?.ddl}</pre>
            </>
          )}
        </Modal>
      )}
    </>
  );
}

function canManageQuery(user: User | null, q: SavedQuery): boolean {
  return isAdmin(user) || (!!user && q.created_by_id === user.id);
}

function SaveQueryModal({
  connectionId,
  sql,
  defaultDatasetId,
  onClose,
  onSaved,
}: {
  connectionId: number;
  sql: string;
  defaultDatasetId?: number;
  onClose: () => void;
  onSaved: (q: SavedQuery) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [datasetId, setDatasetId] = useState<number | "">(defaultDatasetId ?? "");

  const { data: datasets } = useQuery({
    queryKey: ["datasets", { connectionId }],
    queryFn: () => api.get<Dataset[]>(`/datasets?connection_id=${connectionId}`),
  });

  const save = useMutation({
    mutationFn: () =>
      api.post<SavedQuery>("/queries", {
        connection_id: connectionId,
        dataset_id: datasetId === "" ? null : datasetId,
        name: name.trim(),
        description: description.trim(),
        sql,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      }),
    onSuccess: (q) => onSaved(q),
  });

  return (
    <Modal
      title="Save query"
      onClose={onClose}
      footer={
        <>
          <button className="ghost" onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={!name.trim() || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : null}
            Save
          </button>
        </>
      }
    >
      <label>Name</label>
      <input
        type="text"
        value={name}
        autoFocus
        placeholder="e.g. Daily order volume by status"
        onChange={(e) => setName(e.target.value)}
      />
      <label style={{ marginTop: 12 }}>Description</label>
      <input
        type="text"
        value={description}
        placeholder="What this query answers (optional)"
        onChange={(e) => setDescription(e.target.value)}
      />
      <label style={{ marginTop: 12 }}>Tags</label>
      <input
        type="text"
        value={tags}
        placeholder="comma-separated, e.g. triage, revenue"
        onChange={(e) => setTags(e.target.value)}
      />
      <label style={{ marginTop: 12 }}>Pin to dataset (optional)</label>
      <select
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
      <pre className="result" style={{ marginTop: 12, maxHeight: 130, fontSize: 11 }}>{sql}</pre>
      <ErrorBox error={save.error} />
    </Modal>
  );
}

function SavedQueriesRail({
  connectionId,
  editable,
  onLoad,
  onRun,
}: {
  connectionId: number;
  editable: boolean;
  onLoad: (q: SavedQuery) => void;
  onRun: (q: SavedQuery) => void;
}) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [activeTag, setActiveTag] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["saved-queries", { connectionId }],
    queryFn: () => api.get<SavedQuery[]>(`/queries?connection_id=${connectionId}`),
    staleTime: 15_000,
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/queries/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved-queries"] }),
  });

  const allTags = useMemo(() => {
    const s = new Set<string>();
    (data ?? []).forEach((q) => q.tags.forEach((t) => s.add(t)));
    return [...s].sort();
  }, [data]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (data ?? []).filter((q) => {
      if (activeTag && !q.tags.includes(activeTag)) return false;
      if (!needle) return true;
      return (
        q.name.toLowerCase().includes(needle) ||
        q.description.toLowerCase().includes(needle)
      );
    });
  }, [data, search, activeTag]);

  return (
    <div className="card card-pad" style={{ marginTop: 14 }}>
      <h3 style={{ marginBottom: 8 }}>Saved queries</h3>
      <input
        type="text"
        value={search}
        placeholder="Search name / description"
        onChange={(e) => setSearch(e.target.value)}
        style={{ marginTop: 0, fontSize: 12.5 }}
      />
      {allTags.length > 0 && (
        <div className="chip-row" style={{ marginTop: 8 }}>
          {allTags.map((t) => (
            <button
              key={t}
              className={`filter-chip${activeTag === t ? " on" : ""}`}
              onClick={() => setActiveTag(activeTag === t ? null : t)}
            >
              {t}
            </button>
          ))}
        </div>
      )}
      <ErrorBox error={error} />
      <div style={{ marginTop: 10, maxHeight: "48vh", overflowY: "auto" }}>
        {isLoading ? (
          <Spinner label="Loading…" />
        ) : !filtered.length ? (
          <div className="empty" style={{ padding: 14, fontSize: 12.5 }}>
            {data?.length ? "No queries match your filter." : "No saved queries yet. Run SQL and click Save."}
          </div>
        ) : (
          filtered.map((q) => (
            <div key={q.id} className="insight" style={{ padding: "8px 12px" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                <button
                  className="ghost small"
                  style={{ flex: 1, justifyContent: "flex-start", fontWeight: 700, padding: 0, textAlign: "left" }}
                  title="Load into the editor"
                  onClick={() => onLoad(q)}
                >
                  {q.name}
                </button>
                {editable && (
                  <button className="ghost small" title="Load and run" onClick={() => onRun(q)}>
                    <Icon name="play" size={12} />
                  </button>
                )}
                {canManageQuery(user, q) && (
                  <button
                    className="ghost small danger"
                    title="Delete"
                    disabled={remove.isPending}
                    onClick={() => {
                      if (window.confirm(`Delete saved query "${q.name}"?`)) remove.mutate(q.id);
                    }}
                  >
                    <Icon name="x" size={12} />
                  </button>
                )}
              </div>
              {q.description && (
                <div style={{ fontSize: 11, color: "var(--text-light)", margin: "1px 0 4px" }}>{q.description}</div>
              )}
              {(q.tags.length > 0 || q.dataset_id) && (
                <div className="chip-row" style={{ marginTop: 2 }}>
                  {q.dataset_id && <span className="badge kind">pinned</span>}
                  {q.tags.map((t) => (
                    <span key={t} className="badge" style={{ fontSize: 9.5 }}>{t}</span>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ---- multi-tab worksheet state ----
interface TabState {
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

function makeTab(sql = ""): TabState {
  return { id: newTabId(), sql, dirty: false, result: null, error: null, resultLimit: 200, view: "table", chart: { type: "bar", x: "", y: "" }, showFilters: false };
}

const LIMITS = [50, 200, 500, 1000, 2000];

function copyText(text: string): void {
  navigator.clipboard?.writeText(text).catch(() => {
    /* clipboard blocked — silently ignore */
  });
}

function HistoryModal({
  history,
  editable,
  onClose,
  onLoad,
  onClear,
}: {
  history: QueryHistoryEntry[];
  editable: boolean;
  onClose: () => void;
  onLoad: (entry: QueryHistoryEntry, thenRun: boolean) => void;
  onClear: () => void;
}) {
  return (
    <Modal
      title="Query history"
      onClose={onClose}
      wide
      footer={
        <>
          <button className="ghost" onClick={onClose}>Close</button>
          {history.length > 0 && (
            <button
              className="ghost small danger"
              onClick={() => {
                if (window.confirm("Clear this browser's query history?")) onClear();
              }}
            >
              Clear history
            </button>
          )}
        </>
      }
    >
      {history.length === 0 ? (
        <div className="empty" style={{ padding: 18 }}>No queries run yet in this browser.</div>
      ) : (
        <div style={{ maxHeight: "60vh", overflowY: "auto" }}>
          {history.map((h) => (
            <div key={h.id} className="insight" style={{ borderLeftColor: h.ok ? "var(--ok)" : "var(--danger)", padding: "8px 12px" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <span style={{ fontSize: 11.5, color: "var(--text-light)" }}>
                  {timeAgo(h.ranAt)}{h.connectionName ? ` · ${h.connectionName}` : ""}
                </span>
                <span style={{ marginLeft: "auto", fontSize: 11.5, color: h.ok ? "var(--text-light)" : "var(--danger)" }}>
                  {h.ok ? `${fmtNum(h.rowCount)} rows · ${h.elapsedMs} ms` : "error"}
                </span>
              </div>
              <pre className="result" style={{ maxHeight: 110, fontSize: 11, marginTop: 4 }}>{h.sql}</pre>
              {!h.ok && h.error && <div style={{ fontSize: 11, color: "var(--danger)" }}>{h.error}</div>}
              <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                <button className="small" onClick={() => onLoad(h, false)}>Edit</button>
                {editable && <button className="primary small" onClick={() => onLoad(h, true)}>Run</button>}
              </div>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

export default function WorkbenchPage() {
  const { user } = useAuth();
  const editable = canEdit(user);
  const qc = useQueryClient();
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
    queryKey: ["connections"],
    queryFn: () => api.get<Connection[]>("/connections"),
  });
  const { data: dataset } = useQuery({
    queryKey: ["datasets", datasetId],
    queryFn: () => api.get<Dataset>(`/datasets/${datasetId}`),
    enabled: !!datasetId,
  });
  const schemaQuery = useQuery({
    queryKey: ["schema", connectionId],
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
    queryKey: ["saved-query", savedQueryId],
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
    } else {
      const t = makeTab(sql);
      setTabs((prev) => [...prev, t]);
      setActiveId(t.id);
    }
    // React only to the fetched query landing.
  }, [deepLinked.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const suggest = useQuery({
    queryKey: ["suggest", { connectionId, datasetId, runId, exceptionId, checkId }],
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

  const nextLimit = LIMITS.find((n) => n > (active.result ? active.resultLimit : limit)) ?? 2000;
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

  const confirmReplace = () =>
    !active.dirty || !active.sql.trim() || window.confirm("Replace the current query? Unsaved edits will be lost.");

  // Load SQL into the active tab from a saved query / history entry, switching the
  // source if needed. Switching connections clears every tab's stale result.
  const loadSql = (sql: string, sourceConnectionId: number, thenRun: boolean) => {
    if (!confirmReplace()) return;
    const sameConn = sourceConnectionId === connectionId;
    if (!sameConn && sourceConnectionId) {
      setConnectionId(sourceConnectionId);
      setTabs((prev) => prev.map((t) => ({ ...t, result: null, error: null, view: "table" })));
    }
    patchActive({ sql, dirty: false, result: null, error: null });
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
              onLoad={(q) => loadSql(q.sql, q.connection_id, false)}
              onRun={(q) => loadSql(q.sql, q.connection_id, true)}
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
              <div style={{ display: "flex", gap: 6 }}>
                {editable && (
                  <button
                    className="primary small"
                    onClick={() => {
                      editActiveSql(s.sql);
                      runSql(activeId, s.sql);
                    }}
                  >
                    Run
                  </button>
                )}
                <button className="small" onClick={() => editActiveSql(s.sql)}>Edit</button>
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
          onSaved={() => {
            setShowSave(false);
            patchActive({ dirty: false });
            qc.invalidateQueries({ queryKey: ["saved-queries"] });
          }}
        />
      )}

      {showHistory && (
        <HistoryModal
          history={history}
          editable={editable}
          onClose={() => setShowHistory(false)}
          onLoad={(entry, thenRun) => {
            loadSql(entry.sql, entry.connectionId, thenRun);
            setShowHistory(false);
          }}
          onClear={() => setHistory(clearHistory())}
        />
      )}
    </div>
  );
}
