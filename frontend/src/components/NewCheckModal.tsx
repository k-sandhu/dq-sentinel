import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Check, CheckTypeInfo, ColumnInfo, Dataset } from "../api/types";
import CheckParamsForm from "./CheckParamsForm";
import { ErrorBox, Modal, Spinner } from "./ui";

export default function NewCheckModal({
  datasetId,
  onClose,
}: {
  datasetId?: number;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [selectedDatasetId, setSelectedDatasetId] = useState(datasetId ? String(datasetId) : "");
  const [name, setName] = useState("");
  const [checkType, setCheckType] = useState("not_null");
  const [column, setColumn] = useState("");
  const [severity, setSeverity] = useState<Check["severity"]>("error");
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [paramsError, setParamsError] = useState<string | null>(null);

  const datasetQuery = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
    enabled: datasetId == null,
  });
  const typesQuery = useQuery({
    queryKey: ["check-types"],
    queryFn: () => api.get<CheckTypeInfo[]>("/checks/types"),
  });
  const columnsQuery = useQuery({
    queryKey: ["columns", selectedDatasetId],
    queryFn: () => api.get<ColumnInfo[]>(`/datasets/${selectedDatasetId}/columns`),
    enabled: Boolean(selectedDatasetId),
  });

  const selected = useMemo(
    () => typesQuery.data?.find((t) => t.key === checkType) ?? typesQuery.data?.[0],
    [checkType, typesQuery.data],
  );

  const onParamsChange = useCallback((next: Record<string, unknown>, error: string | null) => {
    setParams(next);
    setParamsError(error);
  }, []);

  const create = useMutation({
    mutationFn: () =>
      api.post<Check>("/checks", {
        dataset_id: Number(selectedDatasetId),
        name,
        check_type: selected?.key ?? checkType,
        column_name: selected?.needs_column ? column : null,
        severity,
        params,
        status: "active",
      }),
    onSuccess: (check) => {
      qc.invalidateQueries({ queryKey: ["checks"] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["checks", { datasetId: check.dataset_id }] });
      onClose();
    },
  });

  const canSave = Boolean(selectedDatasetId && selected && (!selected.needs_column || column) && !paramsError);

  return (
    <Modal
      title="New check"
      onClose={onClose}
      wide
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={() => create.mutate()} disabled={create.isPending || !canSave}>
            Create & activate
          </button>
        </>
      }
    >
      <ErrorBox error={create.error || datasetQuery.error || typesQuery.error || columnsQuery.error} />
      {typesQuery.isLoading ? (
        <Spinner label="Loading check types..." />
      ) : (
        <>
          {datasetId == null && (
            <label className="field">
              Dataset <span className="req">*</span>
              <select
                value={selectedDatasetId}
                onChange={(e) => {
                  setSelectedDatasetId(e.target.value);
                  setColumn("");
                }}
              >
                <option value="">- pick a dataset -</option>
                {(datasetQuery.data ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.schema_name ? `${d.schema_name}.` : ""}
                    {d.table_name} ({d.connection_name})
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            Name
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Optional display name" />
          </label>
          <label className="field">
            Check type
            <select
              value={selected?.key ?? checkType}
              onChange={(e) => {
                setCheckType(e.target.value);
                setParams({});
                setColumn("");
              }}
            >
              {typesQuery.data?.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
            <div className="field-hint">{selected?.description}</div>
          </label>
          {selected?.needs_column && (
            <label className="field">
              Column <span className="req">*</span>
              <select value={column} onChange={(e) => setColumn(e.target.value)} disabled={!selectedDatasetId}>
                <option value="">- pick a column -</option>
                {columnsQuery.data?.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name} ({c.dtype})
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            Severity
            <select value={severity} onChange={(e) => setSeverity(e.target.value as Check["severity"])}>
              <option value="info">info</option>
              <option value="warn">warn</option>
              <option value="error">error</option>
            </select>
          </label>
          {selected && <CheckParamsForm typeInfo={selected} params={params} onChange={onParamsChange} />}
        </>
      )}
    </Modal>
  );
}
