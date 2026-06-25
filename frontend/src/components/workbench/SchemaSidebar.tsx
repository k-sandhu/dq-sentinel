import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Ddl, SchemaTable } from "../../api/types";
import { qualifiedRef, quoteIdent } from "../../lib/sqlIdent";
import { Icon, Modal, Spinner } from "../ui";

/** Schema browser: tables/views with expandable columns + a DDL viewer. Clicking a
 *  table or column inserts a dialect-qualified reference into the active editor. */
export function SchemaSidebar({
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
    queryKey: qk.schema.detail(connectionId),
    queryFn: () => api.get<SchemaTable[]>(`/connections/${connectionId}/schema`),
    staleTime: 120_000,
  });
  const ddl = useQuery({
    queryKey: qk.ddl.detail(connectionId, ddlTable),
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
