import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Dataset, SavedQuery } from "../../api/types";
import { ErrorBox, Modal } from "../ui";

/** Save-to-library modal: name/description/tags + optional dataset pin → POST /queries. */
export function SaveQueryModal({
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
    queryKey: qk.datasets.byConnection(connectionId),
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
