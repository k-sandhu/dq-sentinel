import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { Check, Dataset } from "../api/types";
import { canEdit, useAuth } from "../auth";
import ChecksTable from "../components/ChecksTable";
import { EmptyState, ErrorBox, Icon, Modal, Spinner } from "../components/ui";

const FILTERS = ["all", "active", "proposed", "disabled"] as const;

function NewCheckPicker({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [datasetId, setDatasetId] = useState("");
  const { data, isLoading, error } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });

  const go = () => {
    if (!datasetId) return;
    onClose();
    navigate(`/datasets/${datasetId}/checks`);
  };

  return (
    <Modal
      title="New check"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={go} disabled={!datasetId}>
            Continue
          </button>
        </>
      }
    >
      <ErrorBox error={error} />
      {isLoading ? (
        <Spinner />
      ) : !data?.length ? (
        <EmptyState title="No datasets registered" hint="Register a dataset before adding checks." />
      ) : (
        <label className="field">
          Dataset
          <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
            <option value="">Pick a dataset...</option>
            {data.map((d) => (
              <option key={d.id} value={d.id}>
                {d.schema_name ? `${d.schema_name}.` : ""}
                {d.table_name} ({d.connection_name})
              </option>
            ))}
          </select>
          <div className="field-hint">The dataset Checks tab has the check form and profile-aware defaults.</div>
        </label>
      )}
    </Modal>
  );
}

export default function ChecksPage() {
  const { user } = useAuth();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["checks", { filter }],
    queryFn: () => api.get<Check[]>(`/checks${filter === "all" ? "" : `?status=${filter}`}`),
  });

  const needle = search.toLowerCase();
  const shown = (data ?? []).filter(
    (c) =>
      !needle ||
      c.name.toLowerCase().includes(needle) ||
      (c.column_name ?? "").toLowerCase().includes(needle) ||
      c.dataset_name.toLowerCase().includes(needle) ||
      c.check_type.includes(needle),
  );

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Checks</h1>
          <div className="sub">
            Every rule guarding your data, across all datasets
            {data ? ` · ${shown.length} of ${data.length} shown` : ""}
          </div>
        </div>
        {canEdit(user) && (
          <button className="primary" onClick={() => setCreating(true)}>
            <Icon name="plus" size={14} /> New check
          </button>
        )}
      </div>
      <div className="toolbar">
        <input
          type="text"
          placeholder="Search checks by name, column, type or dataset…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: 320, marginTop: 0 }}
        />
        <div className="chip-row">
          {FILTERS.map((f) => (
            <button key={f} className={`filter-chip${filter === f ? " on" : ""}`} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
      </div>
      <ErrorBox error={error} />
      {isLoading ? (
        <Spinner />
      ) : !shown.length ? (
        <div className="card">
          <EmptyState
            title={filter !== "all" || search ? "No checks match your filters" : "No checks yet"}
            hint={
              filter !== "all" || search
                ? "Clear the search or switch the status chip to see more."
                : "Profile a dataset, then generate checks — or add one manually from its Checks tab."
            }
          />
        </div>
      ) : (
        <ChecksTable checks={shown} />
      )}
      {creating && <NewCheckPicker onClose={() => setCreating(false)} />}
    </div>
  );
}
