import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import type { Dataset, TableInfo } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { EmptyState, ErrorBox, Icon, Spinner } from "../components/ui";

export default function ConnectionBrowsePage() {
  const { id } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["tables", id],
    queryFn: () => api.get<TableInfo[]>(`/connections/${id}/tables`),
  });

  const register = useMutation({
    mutationFn: () => {
      const tables = (data ?? [])
        .filter((t) => selected.has(key(t)))
        .map((t) => ({ schema_name: t.schema_name, table_name: t.table_name, kind: t.kind }));
      return api.post<Dataset[]>("/datasets/register", { connection_id: Number(id), tables });
    },
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["tables", id] });
      if (created.length === 1) navigate(`/datasets/${created[0].id}`);
      else navigate("/datasets");
    },
  });

  const key = (t: TableInfo) => `${t.schema_name ?? ""}.${t.table_name}`;
  const toggle = (t: TableInfo) => {
    const k = key(t);
    const next = new Set(selected);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setSelected(next);
  };

  const tables = (data ?? []).filter((t) =>
    t.table_name.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Browse tables</h1>
          <div className="sub">
            <Link to="/connections">← Connections</Link> · pick tables/views to register as monitored datasets
          </div>
        </div>
        {canEdit(user) && (
          <button className="primary" disabled={!selected.size || register.isPending} onClick={() => register.mutate()}>
            <Icon name="plus" size={14} />
            Register {selected.size || ""} dataset{selected.size === 1 ? "" : "s"}
          </button>
        )}
      </div>
      <ErrorBox error={error || register.error} />
      <div className="toolbar">
        <input
          type="text"
          aria-label="Filter tables"
          placeholder="Filter tables…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ maxWidth: 280, marginTop: 0 }}
        />
      </div>
      {isLoading ? (
        <Spinner label="Introspecting source…" />
      ) : !tables.length ? (
        <div className="card">
          <EmptyState
            title={filter ? "No tables match your filter" : "No tables or views found"}
            hint={
              filter
                ? "Clear the filter to see everything the source reported."
                : "The source reported nothing to register — check that the DSN points at the right database and the user can see its schema."
            }
          />
        </div>
      ) : (
        <div className="card table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th style={{ width: 30 }} />
                <th>Table</th>
                <th>Schema</th>
                <th>Kind</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {tables.map((t) => {
                const registered = t.registered_dataset_id != null;
                return (
                  <tr
                    key={key(t)}
                    className={registered ? "" : "clickable"}
                    onClick={() => !registered && toggle(t)}
                  >
                    <td>
                      <input
                        type="checkbox"
                        disabled={registered}
                        checked={selected.has(key(t))}
                        onChange={() => toggle(t)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </td>
                    <td style={{ fontWeight: 600, color: "var(--text-dark)" }}>{t.table_name}</td>
                    <td style={{ color: "var(--text-light)" }}>{t.schema_name ?? "—"}</td>
                    <td><span className="badge kind">{t.kind}</span></td>
                    <td>
                      {registered ? (
                        <Link to={`/datasets/${t.registered_dataset_id}`}>already registered →</Link>
                      ) : (
                        <span style={{ color: "var(--text-light)" }}>not monitored</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
