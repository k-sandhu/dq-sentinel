import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { Check, CheckTypeInfo, ColumnInfo, GenerateResult, Health } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import ChecksTable from "../../components/ChecksTable";
import { ErrorBox, Icon, Modal, Spinner } from "../../components/ui";

function NewCheckModal({ datasetId, onClose }: { datasetId: number; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: types } = useQuery({
    queryKey: ["check-types"],
    queryFn: () => api.get<CheckTypeInfo[]>("/checks/types"),
  });
  const { data: columns } = useQuery({
    queryKey: ["columns", datasetId],
    queryFn: () => api.get<ColumnInfo[]>(`/datasets/${datasetId}/columns`),
  });

  const [checkType, setCheckType] = useState("not_null");
  const [column, setColumn] = useState("");
  const [severity, setSeverity] = useState("error");
  const [paramsText, setParamsText] = useState("{}");

  const selected = types?.find((t) => t.key === checkType);

  const create = useMutation({
    mutationFn: () =>
      api.post<Check>("/checks", {
        dataset_id: datasetId,
        check_type: checkType,
        column_name: selected?.needs_column ? column : null,
        severity,
        params: JSON.parse(paramsText),
        status: "active",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["checks"] });
      onClose();
    },
  });

  return (
    <Modal
      title="New check"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={() => create.mutate()} disabled={create.isPending || (selected?.needs_column && !column)}>
            Create & activate
          </button>
        </>
      }
    >
      <ErrorBox error={create.error} />
      <label className="field">
        Check type
        <select value={checkType} onChange={(e) => { setCheckType(e.target.value); setParamsText("{}"); }}>
          {types?.map((t) => (
            <option key={t.key} value={t.key}>{t.label}</option>
          ))}
        </select>
        <div className="field-hint">{selected?.description}</div>
      </label>
      {selected?.needs_column && (
        <label className="field">
          Column
          <select value={column} onChange={(e) => setColumn(e.target.value)}>
            <option value="">— pick a column —</option>
            {columns?.map((c) => (
              <option key={c.name} value={c.name}>{c.name} ({c.dtype})</option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        Severity
        <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="info">info</option>
          <option value="warn">warn</option>
          <option value="error">error</option>
        </select>
      </label>
      <label className="field">
        Params (JSON)
        <textarea rows={5} value={paramsText} onChange={(e) => setParamsText(e.target.value)} style={{ fontFamily: "var(--mono)", fontSize: 12 }} />
        {selected && selected.params.length > 0 && (
          <div className="field-hint">
            {selected.params.map((p) => (
              <div key={p.name}>
                <code>{p.name}</code> ({p.type}{p.required ? ", required" : ""}) {p.description}
              </div>
            ))}
          </div>
        )}
      </label>
    </Modal>
  );
}

export default function ChecksTab({ datasetId, hasProfile }: { datasetId: number; hasProfile: boolean }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [explore, setExplore] = useState(false);
  const [genResult, setGenResult] = useState<GenerateResult | null>(null);

  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const llm = health?.llm_enabled ?? false;

  const { data: checks, isLoading, error } = useQuery({
    queryKey: ["checks", { datasetId }],
    queryFn: () => api.get<Check[]>(`/checks?dataset_id=${datasetId}`),
  });

  const generate = useMutation({
    mutationFn: () =>
      api.post<GenerateResult>("/checks/generate", { dataset_id: datasetId, use_llm: llm, explore: llm && explore }),
    onSuccess: (result) => {
      setGenResult(result);
      qc.invalidateQueries({ queryKey: ["checks"] });
      qc.invalidateQueries({ queryKey: ["exploration", datasetId] });
    },
  });

  return (
    <div>
      {canEdit(user) && (
        <div className="toolbar">
          <button className="primary" onClick={() => generate.mutate()} disabled={generate.isPending || !hasProfile} title={!hasProfile ? "Profile the dataset first" : undefined}>
            {generate.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="bolt" size={14} />}
            {generate.isPending
              ? explore && llm ? "Exploring data & generating…" : "Generating…"
              : llm ? "Generate checks (AI)" : "Generate checks"}
          </button>
          {llm && (
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, fontWeight: 600 }}>
              <input type="checkbox" checked={explore} onChange={(e) => setExplore(e.target.checked)} style={{ width: "auto", marginTop: 0 }} />
              Explore data first (agent runs SQL to learn more)
            </label>
          )}
          {!llm && <span className="badge" title="Set DQ_LLM_API_KEY + DQ_LLM_MODEL or ANTHROPIC_API_KEY for AI generation">heuristic mode</span>}
          <div className="right">
            <button onClick={() => setCreating(true)}>
              <Icon name="plus" size={13} /> New check
            </button>
          </div>
        </div>
      )}
      <ErrorBox error={error || generate.error} />
      {genResult && (
        <div className="info-box">
          Generated <strong>{genResult.created}</strong> proposal{genResult.created === 1 ? "" : "s"} via{" "}
          <strong>{genResult.mode === "llm" ? "AI" : "heuristics"}</strong>
          {genResult.explored ? " after exploring the data" : ""}
          {genResult.skipped_duplicates ? ` (${genResult.skipped_duplicates} duplicates skipped)` : ""}. Review and activate them below.
        </div>
      )}
      {isLoading ? <Spinner /> : <ChecksTable checks={checks ?? []} showDataset={false} />}
      {creating && <NewCheckModal datasetId={datasetId} onClose={() => setCreating(false)} />}
    </div>
  );
}
