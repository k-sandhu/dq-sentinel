import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import type {
  Connection,
  Dataset,
  Ddl,
  QueryRunResult,
  SchemaTable,
  SuggestResult,
  VizType,
} from "../api/types";
import { canEdit, useAuth } from "../auth";
import Breadcrumbs from "../components/Breadcrumbs";
import PanelChart from "../components/PanelChart";
import { EmptyState, ErrorBox, Icon, Modal, Spinner } from "../components/ui";
import { fmtNum, fmtValue } from "../lib/format";

const BACKTICK_QUOTE_KINDS = new Set(["mysql", "mariadb", "bigquery", "clickhouse"]);
const BRACKET_QUOTE_KINDS = new Set(["mssql", "sqlserver", "sql_server"]);

function quoteIdentifier(identifier: string, connectionKind?: string | null): string {
  const kind = connectionKind?.toLowerCase();
  if (kind && BRACKET_QUOTE_KINDS.has(kind)) {
    return `[${identifier.replaceAll("]", "]]")}]`;
  }
  if (kind && BACKTICK_QUOTE_KINDS.has(kind)) {
    return `\`${identifier.replaceAll("`", "``")}\``;
  }
  return `"${identifier.replaceAll("\"", "\"\"")}"`;
}

function quoteTableIdentifier(table: SchemaTable, connectionKind?: string | null): string {
  const parts = table.schema_name ? [table.schema_name, table.table_name] : [table.table_name];
  return parts.map((part) => quoteIdentifier(part, connectionKind)).join(".");
}

function tableLabel(table: Pick<SchemaTable, "schema_name" | "table_name">): string {
  return table.schema_name ? `${table.schema_name}.${table.table_name}` : table.table_name;
}

function SchemaSidebar({
  connectionId,
  connectionKind,
  onInsert,
}: {
  connectionId: number;
  connectionKind?: string | null;
  onInsert: (text: string) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [ddlTable, setDdlTable] = useState<Pick<SchemaTable, "schema_name" | "table_name"> | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["schema", connectionId],
    queryFn: () => api.get<SchemaTable[]>(`/connections/${connectionId}/schema`),
    staleTime: 120_000,
  });
  const ddl = useQuery({
    queryKey: ["ddl", connectionId, ddlTable?.schema_name, ddlTable?.table_name],
    queryFn: () => {
      const qs = new URLSearchParams({ table: ddlTable!.table_name });
      if (ddlTable!.schema_name) qs.set("schema", ddlTable!.schema_name);
      return api.get<Ddl>(`/connections/${connectionId}/ddl?${qs.toString()}`);
    },
    enabled: !!ddlTable,
  });

  if (isLoading) return <Spinner label="Introspecting…" />;
  return (
    <div style={{ fontSize: 12.5, maxHeight: "70vh", overflowY: "auto" }}>
      {(data ?? []).map((t) => {
        const key = tableLabel(t);
        const expanded = open.has(key);
        const displayName = tableLabel(t);
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
                {expanded ? "▾" : "▸"} {displayName}
                {t.kind === "view" && <span className="badge kind" style={{ marginLeft: 4 }}>view</span>}
              </button>
              <button className="ghost small" title="Insert quoted table name" onClick={() => onInsert(quoteTableIdentifier(t, connectionKind))}>
                <Icon name="plus" size={11} />
              </button>
              <button className="ghost small" title="View definition (DDL)" onClick={() => setDdlTable({ schema_name: t.schema_name, table_name: t.table_name })}>
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
                    onClick={() => onInsert(quoteIdentifier(c.name, connectionKind))}
                    title="Click to insert quoted column name"
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
        <Modal title={`Definition of ${tableLabel(ddlTable)}`} onClose={() => setDdlTable(null)} wide>
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

export default function WorkbenchPage() {
  const { user } = useAuth();
  const editable = canEdit(user);
  const [params] = useSearchParams();
  const datasetId = params.get("dataset_id") ? Number(params.get("dataset_id")) : undefined;
  const runId = params.get("run_id") ? Number(params.get("run_id")) : undefined;
  const exceptionId = params.get("exception_id") ? Number(params.get("exception_id")) : undefined;
  const checkId = params.get("check_id") ? Number(params.get("check_id")) : undefined;

  const [connectionId, setConnectionId] = useState<number | null>(
    params.get("connection_id") ? Number(params.get("connection_id")) : null,
  );
  const [sql, setSql] = useState("");
  const [limit, setLimit] = useState(200);
  const [result, setResult] = useState<QueryRunResult | null>(null);
  const [chart, setChart] = useState(false);
  const [chartType, setChartType] = useState<VizType>("bar");
  const [chartX, setChartX] = useState<string>("");
  const [chartY, setChartY] = useState<string>("");
  const activeConnectionId = useRef<number | null>(connectionId);

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

  useEffect(() => {
    activeConnectionId.current = connectionId;
  }, [connectionId]);

  const activeConnection = useMemo(
    () => connections?.find((c) => c.id === connectionId) ?? null,
    [connections, connectionId],
  );

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
    mutationFn: ({ connectionId: runConnectionId, sql: q, rowLimit }: { connectionId: number; sql: string; rowLimit: number }) =>
      api.post<QueryRunResult>("/query/run", { connection_id: runConnectionId, sql: q, limit: rowLimit }),
    onSuccess: (r, variables) => {
      if (variables.connectionId !== activeConnectionId.current) return;
      setResult(r);
      setChartX(r.columns[0] ?? "");
      setChartY(r.columns[r.columns.length - 1] ?? "");
    },
  });

  const resetQueryState = (clearSql: boolean) => {
    if (clearSql) setSql("");
    setResult(null);
    setChart(false);
    setChartX("");
    setChartY("");
    run.reset();
  };

  const runSql = (q: string) => {
    if (!connectionId) return;
    run.mutate({ connectionId, sql: q, rowLimit: limit });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && editable && sql.trim()) {
      e.preventDefault();
      runSql(sql);
    }
  };

  const insert = (text: string) =>
    setSql((s) => (s ? `${s.trimEnd()} ${text}` : `SELECT * FROM ${text} LIMIT 50`));

  const numericColumns = useMemo(() => {
    if (!result) return [];
    return result.columns.filter((_c, i) =>
      result.rows.some((r) => typeof r[i] === "number"),
    );
  }, [result]);
  const datasetName = dataset ? `${dataset.schema_name ? `${dataset.schema_name}.` : ""}${dataset.table_name}` : null;

  return (
    <div className="page" style={{ maxWidth: 1500 }}>
      <Breadcrumbs
        items={
          dataset && datasetName
            ? [{ label: "Datasets", to: "/datasets" }, { label: datasetName, to: `/datasets/${dataset.id}` }, { label: "Workbench" }]
            : [{ label: "Workbench" }]
        }
      />
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
            onChange={(e) => {
              const nextConnectionId = e.target.value ? Number(e.target.value) : null;
              if (nextConnectionId === connectionId) return;
              setConnectionId(nextConnectionId);
              activeConnectionId.current = nextConnectionId;
              resetQueryState(true);
            }}
            style={{ marginTop: 0, width: 220 }}
          >
            {connections?.map((c) => (
              <option key={c.id} value={c.id}>{c.name} ({c.kind})</option>
            ))}
          </select>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "230px 1fr 320px", gap: 16, alignItems: "start" }}>
        <div className="card card-pad">
          <h3>Schema</h3>
          {connectionId ? (
            <SchemaSidebar connectionId={connectionId} connectionKind={activeConnection?.kind} onInsert={insert} />
          ) : (
            <div className="empty">Pick a connection</div>
          )}
        </div>

        <div>
          <div className="card card-pad" style={{ marginBottom: 14 }}>
            <textarea
              value={sql}
              onChange={(e) => setSql(e.target.value)}
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
                onClick={() => runSql(sql)}
                title="Ctrl+Enter"
              >
                {run.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="play" size={13} />}
                Run
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
                      setSql(s.sql);
                      runSql(s.sql);
                    }}
                  >
                    Run
                  </button>
                )}
                <button className="small" onClick={() => setSql(s.sql)}>Edit</button>
              </div>
            </div>
          ))}
          {suggest.data && suggest.data.suggestions.length === 0 && (
            <div className="empty" style={{ padding: 14 }}>No suggestions for this context.</div>
          )}
        </div>
      </div>
    </div>
  );
}
