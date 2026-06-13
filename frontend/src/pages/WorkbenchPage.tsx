import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import type { KeyboardEvent } from "react";
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
import { Breadcrumbs, EmptyState, ErrorBox, Icon, Modal, Spinner } from "../components/ui";
import { fmtNum, fmtValue } from "../lib/format";
import { qualifiedRef, quoteIdent } from "../lib/sqlIdent";

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

  if (isLoading) return <Spinner label="Introspecting…" />;
  return (
    <div style={{ fontSize: 12.5, maxHeight: "70vh", overflowY: "auto" }}>
      {(data ?? []).map((t) => {
        const key = t.table_name;
        const expanded = open.has(key);
        return (
          <div key={key} style={{ marginBottom: 2 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <button
                className="ghost small"
                style={{ flex: 1, justifyContent: "flex-start", fontFamily: "var(--mono)", fontSize: 12 }}
                onClick={() => {
                  const next = new Set(open);
                  if (expanded) next.delete(key);
                  else next.add(key);
                  setOpen(next);
                }}
                title={`${t.kind} — click to ${expanded ? "collapse" : "expand"}`}
              >
                {expanded ? "▾" : "▸"} {t.table_name}
                {t.kind === "view" && <span className="badge kind" style={{ marginLeft: 4 }}>view</span>}
              </button>
              <button
                className="ghost small"
                title="Insert schema-qualified table"
                onClick={() => onInsert(qualifiedRef(t.schema_name, t.table_name, dialect), { table: true })}
              >
                <Icon name="plus" size={11} />
              </button>
              <button className="ghost small" title="View definition (DDL)" onClick={() => setDdlTable(t.table_name)}>
                <Icon name="book" size={11} />
              </button>
            </div>
            {expanded && (
              <div style={{ paddingLeft: 22 }}>
                {t.columns.map((c) => (
                  <div
                    key={c.name}
                    className="clickable"
                    style={{ display: "flex", justifyContent: "space-between", padding: "1.5px 4px", cursor: "pointer", borderRadius: 4 }}
                    onClick={() => onInsert(quoteIdent(c.name, dialect))}
                    title="Click to insert"
                  >
                    <span style={{ fontFamily: "var(--mono)", fontSize: 11.5 }}>{c.name}</span>
                    <span style={{ color: "var(--text-light)", fontSize: 10.5 }}>{c.dtype.toLowerCase().slice(0, 12)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
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
    </div>
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
  const [sql, setSql] = useState("");
  const [dirty, setDirty] = useState(false);
  const [limit, setLimit] = useState(200);
  const [result, setResult] = useState<QueryRunResult | null>(null);
  const [chart, setChart] = useState(false);
  const [chartType, setChartType] = useState<VizType>("bar");
  const [chartX, setChartX] = useState<string>("");
  const [chartY, setChartY] = useState<string>("");
  const [showSave, setShowSave] = useState(false);

  const { data: connections } = useQuery({
    queryKey: ["connections"],
    queryFn: () => api.get<Connection[]>("/connections"),
  });
  const { data: dataset } = useQuery({
    queryKey: ["datasets", datasetId],
    queryFn: () => api.get<Dataset>(`/datasets/${datasetId}`),
    enabled: !!datasetId,
  });

  useEffect(() => {
    if (!connectionId && dataset) setConnectionId(dataset.connection_id);
    else if (!connectionId && !datasetId && connections?.length) setConnectionId(connections[0].id);
  }, [dataset, connections, connectionId, datasetId]);

  // Deep-link: preload a saved query's SQL (and its connection) when ?saved_query_id= is present.
  const deepLinked = useQuery({
    queryKey: ["saved-query", savedQueryId],
    queryFn: () => api.get<SavedQuery>(`/queries/${savedQueryId}`),
    enabled: !!savedQueryId,
  });
  useEffect(() => {
    if (deepLinked.data && !dirty && !sql) {
      setSql(deepLinked.data.sql);
      setConnectionId(deepLinked.data.connection_id);
    }
    // only react to the fetched query landing; intentionally not depending on sql/dirty
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepLinked.data]);

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

  const run = useMutation({
    mutationFn: (q: string) =>
      api.post<QueryRunResult>("/query/run", { connection_id: connectionId, sql: q, limit }),
    onSuccess: (r) => {
      setResult(r);
      setChartX(r.columns[0] ?? "");
      setChartY(r.columns[r.columns.length - 1] ?? "");
    },
  });

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && editable && sql.trim()) {
      e.preventDefault();
      run.mutate(sql);
    }
  };

  // The engine kind of the selected connection drives identifier quoting
  // (Connection.kind is one of the dialects.py registry kinds, e.g. "postgresql").
  const dialect = useMemo(
    () => connections?.find((c) => c.id === connectionId)?.kind ?? null,
    [connections, connectionId],
  );

  const editSql = (value: string) => {
    setSql(value);
    setDirty(true);
  };

  // Insert a schema-browser identifier into the editor. Table references seed an
  // empty editor with a schema-qualified `SELECT * … LIMIT 50`; columns (and
  // tables added to existing SQL) are appended verbatim — the caller has already
  // quoted/qualified the text for the active dialect.
  const insert = (text: string, opts?: { table?: boolean }) =>
    editSql(
      sql.trim()
        ? `${sql.trimEnd()} ${text}`
        : opts?.table
          ? `SELECT * FROM ${text} LIMIT 50`
          : text,
    );

  // Load a saved query into the editor, confirming first if there are unsaved edits.
  const loadSaved = (q: SavedQuery, thenRun = false) => {
    if (dirty && !window.confirm("Replace the current query? Unsaved edits will be lost.")) return;
    setSql(q.sql);
    setDirty(false);
    if (q.connection_id !== connectionId) setConnectionId(q.connection_id);
    if (thenRun && editable && q.connection_id === connectionId) run.mutate(q.sql);
  };

  // Switching the source must not leave SQL/results pointed at the old database.
  const changeConnection = (id: number) => {
    if (id === connectionId) return;
    setConnectionId(id);
    setSql("");
    setDirty(false);
    setResult(null);
    setChart(false);
    run.reset();
  };

  const numericColumns = useMemo(() => {
    if (!result) return [];
    return result.columns.filter((_c, i) =>
      result.rows.some((r) => typeof r[i] === "number"),
    );
  }, [result]);

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

      <div style={{ display: "grid", gridTemplateColumns: "230px 1fr 320px", gap: 16, alignItems: "start" }}>
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
              onLoad={(q) => loadSaved(q)}
              onRun={(q) => loadSaved(q, true)}
            />
          )}
        </div>

        <div>
          <div className="card card-pad" style={{ marginBottom: 14 }}>
            <textarea
              value={sql}
              onChange={(e) => editSql(e.target.value)}
              onKeyDown={onKeyDown}
              rows={7}
              placeholder={"SELECT status, COUNT(*) AS n\nFROM orders\nGROUP BY 1\nORDER BY n DESC"}
              style={{ fontFamily: "var(--mono)", fontSize: 13, marginTop: 0 }}
              spellCheck={false}
            />
            <div className="toolbar" style={{ marginBottom: 0, marginTop: 10 }}>
              <button
                className="primary"
                disabled={!editable || !sql.trim() || !connectionId || run.isPending}
                onClick={() => run.mutate(sql)}
                title="Ctrl+Enter"
              >
                {run.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="play" size={13} />}
                Run
              </button>
              <button
                className="small"
                disabled={!editable || !sql.trim() || !connectionId}
                onClick={() => setShowSave(true)}
                title="Save this query to the shared team library"
              >
                <Icon name="plus" size={12} /> Save
              </button>
              <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} style={{ marginTop: 0, width: 120 }}>
                {[50, 200, 500, 1000, 2000].map((n) => (
                  <option key={n} value={n}>limit {n}</option>
                ))}
              </select>
              <span style={{ fontSize: 11.5, color: "var(--text-light)" }}>
                Read-only · single SELECT/WITH · <span className="kbd">Ctrl</span>+<span className="kbd">Enter</span> to run
              </span>
            </div>
            <ErrorBox error={run.error} />
          </div>

          {result && (
            <div className="card">
              <div className="card-pad" style={{ display: "flex", gap: 12, alignItems: "center", paddingBottom: 10, flexWrap: "wrap" }}>
                <strong>{fmtNum(result.row_count)} rows</strong>
                <span style={{ color: "var(--text-light)", fontSize: 12 }}>{result.elapsed_ms} ms</span>
                {result.truncated && <span className="badge" title="Increase the limit to fetch more">truncated at limit</span>}
                <div className="right" style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
                  {numericColumns.length > 0 && result.columns.length >= 2 && (
                    <button className="small" onClick={() => setChart(!chart)}>
                      {chart ? "Table" : "Chart"}
                    </button>
                  )}
                </div>
              </div>
              {chart ? (
                <div className="card-pad" style={{ paddingTop: 0 }}>
                  <div className="toolbar">
                    <select value={chartType} onChange={(e) => setChartType(e.target.value as VizType)} style={{ marginTop: 0, width: 100 }}>
                      {["bar", "line", "area", "pie"].map((t) => <option key={t}>{t}</option>)}
                    </select>
                    <select value={chartX} onChange={(e) => setChartX(e.target.value)} style={{ marginTop: 0, width: 150 }}>
                      {result.columns.map((c) => <option key={c}>{c}</option>)}
                    </select>
                    <select value={chartY} onChange={(e) => setChartY(e.target.value)} style={{ marginTop: 0, width: 150 }}>
                      {numericColumns.map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </div>
                  <PanelChart
                    columns={result.columns}
                    rows={result.rows}
                    viz={{ type: chartType, x: chartX, y: chartY }}
                    height={300}
                  />
                </div>
              ) : (
                <div className="table-wrap" style={{ maxHeight: 460, overflowY: "auto" }}>
                  <table className="data">
                    <thead>
                      <tr>{result.columns.map((c) => <th key={c}>{c}</th>)}</tr>
                    </thead>
                    <tbody>
                      {result.rows.map((r, i) => (
                        <tr key={i}>
                          {r.map((v, j) => (
                            <td key={j} className="mono" style={{ whiteSpace: "nowrap", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }}>
                              {fmtValue(v)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          {!result && !run.isPending && (
            <div className="card">
              <EmptyState title="Results appear here" hint="Write SQL, click a suggestion, or insert a table from the schema browser." />
            </div>
          )}
        </div>

        <div className="card card-pad">
          <h3>
            Suggested queries{" "}
            {suggest.data && (
              <span className={`badge ${suggest.data.mode === "llm" ? "ai" : ""}`}>
                {suggest.data.mode === "llm" ? "AI" : "heuristic"}
              </span>
            )}
          </h3>
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
                      editSql(s.sql);
                      run.mutate(s.sql);
                    }}
                  >
                    Run
                  </button>
                )}
                <button className="small" onClick={() => editSql(s.sql)}>Edit</button>
              </div>
            </div>
          ))}
          {suggest.data && suggest.data.suggestions.length === 0 && (
            <div className="empty" style={{ padding: 14 }}>No suggestions for this context.</div>
          )}
        </div>
      </div>

      {showSave && connectionId && (
        <SaveQueryModal
          connectionId={connectionId}
          sql={sql}
          defaultDatasetId={dataset?.connection_id === connectionId ? datasetId : undefined}
          onClose={() => setShowSave(false)}
          onSaved={() => {
            setShowSave(false);
            setDirty(false);
            qc.invalidateQueries({ queryKey: ["saved-queries"] });
          }}
        />
      )}
    </div>
  );
}
