import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { api, ApiError } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type {
  CheckTypeInfo,
  ColumnInfo,
  ContractClauseConformance,
  DataContract,
  DataContractApplyResult,
  DataContractConformance,
  DataContractDiff,
  DataContractExport,
  DataContractVersion,
  Dataset,
  Severity,
} from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import { fmtDateTime } from "../../lib/format";
import { useUnsavedGuard } from "../../lib/useUnsavedGuard";
import CheckParamsForm, { validateParams } from "../../components/CheckParamsForm";
import { invalidateCheckCaches } from "../../components/ChecksTable";
import { useConfirm } from "../../components/confirm";
import { ErrorBox, Icon, Modal, Spinner, StatusPill } from "../../components/ui";

/* ---------------------------------------------------------------------------
 * Stable row identity (#283)
 *
 * The schema/quality editors are lists of rows whose own inputs edit the row's
 * values. A React `key` derived from any of those values (the old
 * `key={`${col.name}-${i}`}`) changes on every keystroke, so React unmounts and
 * remounts the row — which destroys the focused input and made renaming or
 * hand-adding a contract column impossible.
 *
 * Rows therefore carry `_rowId`: a client-only identifier minted once when the
 * row enters the draft, never derived from and never touched by an edit. It is
 * stripped by `normalizeSpec`, so it reaches neither the API payload, the ODCS
 * export, nor the dirty comparison.
 * ------------------------------------------------------------------------- */
let rowSeq = 0;
function nextRowId(): string {
  rowSeq += 1;
  return `row-${rowSeq}`;
}

interface RowIdentity {
  /** Client-only React key. Stripped before the spec leaves this component. */
  _rowId?: string;
}

interface ContractColumn extends RowIdentity {
  name: string;
  dtype?: string;
  nullable?: boolean;
  required?: boolean;
  description?: string;
}

interface QualityClause extends RowIdentity {
  id: string;
  name: string;
  check_type: string;
  column?: string | null;
  params?: Record<string, unknown>;
  severity?: Severity;
  schedule_expr?: string;
  rationale?: string;
}

interface ContractSpec {
  version?: number;
  schema?: {
    columns?: ContractColumn[];
    allow_extra_columns?: boolean;
    compare_types?: boolean;
    enforce_nullable?: boolean;
  };
  freshness?: {
    column?: string;
    max_age_hours?: number;
    severity?: Severity;
    schedule_expr?: string;
  };
  volume?: {
    min_rows?: number;
    baseline_rows?: number;
    severity?: Severity;
    schedule_expr?: string;
  };
  quality?: QualityClause[];
  owner?: { name?: string; importance?: string };
  consumers?: { name: string; description?: string }[];
  terms?: string;
  materialized?: unknown;
}

function asSpec(value: Record<string, unknown> | null | undefined): ContractSpec {
  return { ...(value ?? {}) } as ContractSpec;
}

function stripRowId<T extends RowIdentity>(row: T): T {
  if (row._rowId === undefined) return row;
  const copy = { ...row };
  delete copy._rowId;
  return copy;
}

function normalizeSpec(spec: ContractSpec): ContractSpec {
  return {
    version: spec.version ?? 1,
    schema: {
      columns: (spec.schema?.columns ?? []).map(stripRowId),
      allow_extra_columns: spec.schema?.allow_extra_columns ?? true,
      compare_types: spec.schema?.compare_types ?? false,
      enforce_nullable: spec.schema?.enforce_nullable ?? false,
    },
    freshness: spec.freshness ?? {},
    volume: spec.volume ?? {},
    quality: (spec.quality ?? []).map(stripRowId),
    owner: spec.owner ?? {},
    consumers: spec.consumers ?? [],
    terms: spec.terms ?? "",
    ...(spec.materialized !== undefined ? { materialized: spec.materialized } : {}),
  };
}

/** Mint a fresh row id for every row of a spec that is entering the draft. */
function withRowIds(spec: ContractSpec): ContractSpec {
  return {
    ...spec,
    schema: {
      ...(spec.schema ?? {}),
      columns: (spec.schema?.columns ?? []).map((c) => ({ ...c, _rowId: nextRowId() })),
    },
    quality: (spec.quality ?? []).map((q) => ({ ...q, _rowId: nextRowId() })),
  };
}

/** Clause ids are semantic (they key conformance and the materialized check's
 *  rationale marker), so a new clause must not reuse one that already exists —
 *  `quality-${length + 1}` collided after a middle clause was removed. */
function nextClauseId(existing: QualityClause[]): string {
  const taken = new Set(existing.map((q) => q.id));
  let n = existing.length + 1;
  while (taken.has(`quality-${n}`)) n += 1;
  return `quality-${n}`;
}

function clauseTone(status: string) {
  return status === "pass" ? "ok" : status === "breached" ? "danger" : "neutral";
}

/** One-line preview of a clause's params for the row button. */
function paramsSummary(params: Record<string, unknown> | undefined): string {
  const entries = Object.entries(params ?? {}).filter(
    ([, v]) => v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && v.length === 0),
  );
  if (!entries.length) return "";
  return entries
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

function clauseLabel(clause: ContractClauseConformance) {
  return `${clause.kind}: ${clause.label}`;
}

const DELETE_ROLE_HINT = "Editor or admin role required to delete a data contract";

export default function ContractTab({ dataset }: { dataset: Dataset }) {
  const { user } = useAuth();
  const editable = canEdit(user);
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [mode, setMode] = useState<"form" | "yaml" | "versions">("form");
  const [name, setName] = useState("");
  const [version, setVersion] = useState("0.1.0");
  const [draft, setDraft] = useState<ContractSpec>(normalizeSpec({}));
  const [yamlText, setYamlText] = useState("");
  const [saved, setSaved] = useState(false);
  const [deletedId, setDeletedId] = useState<number | null>(null);
  const [paramsRowId, setParamsRowId] = useState<string | null>(null);

  const contractQuery = useQuery({
    queryKey: qk.contract.detail(dataset.id),
    queryFn: () => api.get<DataContract>(`/datasets/${dataset.id}/contract`),
    retry: (count, error) => error instanceof ApiError && error.status === 404 ? false : count < 2,
  });
  // TanStack keeps the last good data when a refetch errors, so the 404 that
  // follows a delete would otherwise leave the tab happily editing a contract
  // that no longer exists. Drop it explicitly until the refetch settles (if the
  // dataset has an older contract, that one loads and takes over normally).
  const contract = contractQuery.data?.id === deletedId ? undefined : contractQuery.data;

  const columnsQuery = useQuery({
    queryKey: qk.columns.detail(dataset.id),
    queryFn: () => api.get<ColumnInfo[]>(`/datasets/${dataset.id}/columns`),
  });

  // Same registry the Checks tab uses, so quality clauses get typed param fields
  // instead of hand-written JSON (#294).
  const checkTypesQuery = useQuery({
    queryKey: qk.checkTypes.list(),
    queryFn: () => api.get<CheckTypeInfo[]>("/checks/types"),
  });

  const conformanceQuery = useQuery({
    queryKey: qk.contractConformance.detail(dataset.id, contract?.id),
    queryFn: () => api.get<DataContractConformance>(`/datasets/${dataset.id}/contract/${contract!.id}/conformance`),
    enabled: !!contract,
    refetchInterval: 30_000,
  });

  const exportQuery = useQuery({
    queryKey: qk.contractExport.detail(dataset.id, contract?.id),
    queryFn: () => api.get<DataContractExport>(`/datasets/${dataset.id}/contract/${contract!.id}/export?format=odcs`),
    enabled: !!contract && mode === "yaml",
  });

  const versionsQuery = useQuery({
    queryKey: qk.contractVersions.detail(dataset.id, contract?.id),
    queryFn: () => api.get<DataContractVersion[]>(`/datasets/${dataset.id}/contract/${contract!.id}/versions`),
    enabled: !!contract && mode === "versions",
  });

  const versions = versionsQuery.data ?? [];
  const diffQuery = useQuery({
    queryKey: qk.contractDiff.detail(dataset.id, contract?.id, versions[1]?.id, versions[0]?.id),
    queryFn: () =>
      api.get<DataContractDiff>(
        `/datasets/${dataset.id}/contract/${contract!.id}/versions/${versions[1].id}/diff?to_version_id=${versions[0].id}`,
      ),
    enabled: !!contract && mode === "versions" && versions.length >= 2,
  });

  useEffect(() => {
    if (!contract) return;
    setName(contract.name);
    setVersion(contract.version);
    setDraft(withRowIds(normalizeSpec(asSpec(contract.spec))));
  }, [contract]);

  useEffect(() => {
    if (exportQuery.data?.yaml && !yamlText) setYamlText(exportQuery.data.yaml);
  }, [exportQuery.data, yamlText]);

  // The Contract tab holds as much typing as Knowledge (#D4), so it gets the same
  // guard: the dataset tab strip, any link out of the page, and tab close all ask
  // before the draft is thrown away (#284).
  const dirty = useMemo(() => {
    if (!contract) return false;
    return (
      name !== contract.name ||
      version !== contract.version ||
      JSON.stringify(normalizeSpec(draft)) !== JSON.stringify(normalizeSpec(asSpec(contract.spec)))
    );
  }, [contract, name, version, draft]);

  useUnsavedGuard(dirty, "Your unsaved edits to this data contract will be discarded.");

  const create = useMutation({
    mutationFn: () => api.post<DataContract>(`/datasets/${dataset.id}/contract`, {}),
    onSuccess: (created) => {
      setDeletedId(null);
      qc.setQueryData(qk.contract.detail(dataset.id), created);
      qc.invalidateQueries({ queryKey: qk.contract.all });
    },
  });

  const save = useMutation({
    mutationFn: () =>
      api.patch<DataContract>(`/datasets/${dataset.id}/contract/${contract!.id}`, {
        name,
        version,
        spec: normalizeSpec(draft),
      }),
    onSuccess: (updated) => {
      qc.setQueryData(qk.contract.detail(dataset.id), updated);
      qc.invalidateQueries({ queryKey: qk.contractVersions.detail(dataset.id, updated.id) });
      setSaved(true);
      setTimeout(() => setSaved(false), 2200);
    },
  });

  const activate = useMutation({
    mutationFn: () => api.post<DataContractApplyResult>(`/datasets/${dataset.id}/contract/${contract!.id}/activate`),
    onSuccess: (result) => {
      qc.setQueryData(qk.contract.detail(dataset.id), result.contract);
      qc.invalidateQueries({ queryKey: qk.checks.all });
      qc.invalidateQueries({ queryKey: qk.contractConformance.detail(dataset.id, result.contract.id) });
      qc.invalidateQueries({ queryKey: qk.contractVersions.detail(dataset.id, result.contract.id) });
    },
  });

  // DELETE /datasets/{id}/contract/{contract_id} — editor role, 204 (#289).
  const remove = useMutation({
    mutationFn: (target: DataContract) => api.del<void>(`/datasets/${dataset.id}/contract/${target.id}`),
    onSuccess: (_result, target) => {
      setDeletedId(target.id);
      setMode("form");
      setSaved(false);
      setParamsRowId(null);
      qc.removeQueries({ queryKey: qk.contractConformance.detail(dataset.id, target.id) });
      qc.removeQueries({ queryKey: qk.contractExport.detail(dataset.id, target.id) });
      qc.removeQueries({ queryKey: qk.contractVersions.detail(dataset.id, target.id) });
      qc.removeQueries({ queryKey: qk.contractDiff.all });
      // Refetch: the dataset may still have an older contract behind this one.
      qc.invalidateQueries({ queryKey: qk.contract.all });
      // The server archives the checks this contract materialized, which moves
      // the dataset/dashboard "active checks" rollups too (#285).
      invalidateCheckCaches(qc);
    },
  });

  const importYaml = useMutation({
    mutationFn: () => api.post<DataContract>(`/datasets/${dataset.id}/contract/import`, { yaml: yamlText }),
    onSuccess: (created) => {
      setDeletedId(null);
      qc.setQueryData(qk.contract.detail(dataset.id), created);
      qc.invalidateQueries({ queryKey: qk.contract.all });
      setMode("form");
    },
  });

  const materializedCheckIds = useMemo(() => {
    const mat = (draft.materialized ?? {}) as { checks?: { check_id?: number }[] };
    return new Set((mat.checks ?? []).map((c) => c.check_id).filter((id): id is number => typeof id === "number"));
  }, [draft.materialized]);

  const addSourceColumns = () => {
    const existing = new Set((draft.schema?.columns ?? []).map((c) => c.name.toLowerCase()));
    const additions = (columnsQuery.data ?? [])
      .filter((c) => !existing.has(c.name.toLowerCase()))
      .map((c) => ({ name: c.name, dtype: c.dtype, nullable: c.nullable, required: true, _rowId: nextRowId() }));
    setDraft((spec) => ({
      ...spec,
      schema: { ...(spec.schema ?? {}), columns: [...(spec.schema?.columns ?? []), ...additions] },
    }));
  };

  const askDelete = async () => {
    if (!contract) return;
    const target = contract;
    const materialized = materializedCheckIds.size;
    const ok = await confirm({
      title: "Delete data contract",
      danger: true,
      confirmLabel: "Delete contract",
      typeToConfirm: target.name,
      body: (
        <>
          <p style={{ marginTop: 0 }}>
            This permanently deletes <strong>{target.name}</strong> (v{target.version}) and all{" "}
            {target.version_count} saved version snapshot{target.version_count === 1 ? "" : "s"}. Version history
            and diffs cannot be recovered.
          </p>
          <p>
            The checks this contract materialized{materialized ? ` (${materialized} in the current spec)` : ""} are{" "}
            <strong>archived, not deleted</strong>: they stop running immediately and disappear from the Checks
            tab. Their past runs and exceptions are kept in the database, but an archived check{" "}
            <strong>cannot be restored from the UI</strong> — you would have to recreate it on the Checks tab, or
            activate a new contract, which materializes fresh checks.
          </p>
          <p style={{ marginBottom: 0 }}>
            The dataset, its profile, and any checks created outside this contract are untouched.
          </p>
        </>
      ),
    });
    if (ok) remove.mutate(target);
  };

  const quality = draft.quality ?? [];
  const schemaColumns = draft.schema?.columns ?? [];
  const conformance = conformanceQuery.data;
  const checkTypes = checkTypesQuery.data;

  const editingIndex = paramsRowId === null ? -1 : quality.findIndex((q) => q._rowId === paramsRowId);
  const editingClause = editingIndex >= 0 ? quality[editingIndex] : undefined;
  const editingType = checkTypes?.find((t) => t.key === editingClause?.check_type);

  if (contractQuery.isLoading) return <Spinner label="Loading contract..." />;
  if (
    !contract &&
    (deletedId !== null || (contractQuery.error instanceof ApiError && contractQuery.error.status === 404))
  ) {
    return (
      <div className="contract-empty">
        <div className="card card-pad">
          <h3>No data contract yet</h3>
          {deletedId !== null && (
            <div className="info-box">
              Contract deleted. Any checks it materialized were archived, not deleted: they stopped running and
              are no longer listed on the Checks tab. Their past runs and exceptions are kept, but archived
              checks cannot be restored from the UI — recreate them on the Checks tab, or activate a new
              contract to materialize fresh ones.
            </div>
          )}
          <p className="muted">
            Create a draft from the current profile, table knowledge, and source schema, or import an ODCS YAML
            contract.
          </p>
          <ErrorBox error={create.error || importYaml.error} />
          {editable && (
            <div className="toolbar">
              <button className="primary" onClick={() => create.mutate()} disabled={create.isPending}>
                <Icon name="shield" size={14} /> Create draft
              </button>
            </div>
          )}
        </div>
        <div className="card card-pad">
          <h3>Import ODCS YAML</h3>
          <textarea
            className="contract-yaml"
            value={yamlText}
            onChange={(e) => setYamlText(e.target.value)}
            placeholder="apiVersion: v3.0.0&#10;kind: DataContract&#10;name: ..."
            disabled={!editable}
          />
          {editable && (
            <button onClick={() => importYaml.mutate()} disabled={importYaml.isPending || !yamlText.trim()}>
              <Icon name="plus" size={13} /> Import
            </button>
          )}
        </div>
      </div>
    );
  }
  if (contractQuery.error) return <ErrorBox error={contractQuery.error} />;
  if (!contract) return null;

  return (
    <div className="contract-root">
      <div className="contract-head">
        <div>
          <div className="contract-title-line">
            <h3>{contract.name}</h3>
            <StatusPill value={contract.status} />
            {conformance && <StatusPill value={conformance.status} />}
          </div>
          <div className="muted">
            Version {contract.version} · {contract.version_count} saved snapshot{contract.version_count === 1 ? "" : "s"}
            {contract.activated_at ? ` · activated ${fmtDateTime(contract.activated_at)}` : ""}
          </div>
        </div>
        <div className="header-actions">
          {editable && (
            <>
              <button onClick={() => save.mutate()} disabled={save.isPending}>
                <Icon name="check" size={13} /> {save.isPending ? "Saving..." : "Save"}
              </button>
              <button className="primary" onClick={() => activate.mutate()} disabled={activate.isPending}>
                <Icon name="play" size={13} /> {activate.isPending ? "Activating..." : "Activate"}
              </button>
            </>
          )}
          {/* Shown to everyone, disabled with the reason for viewers, so the
              lifecycle is discoverable instead of silently missing (#289). */}
          <button
            type="button"
            className="danger"
            onClick={askDelete}
            disabled={!editable || remove.isPending}
            title={editable ? "Delete this contract and its version history" : DELETE_ROLE_HINT}
          >
            <Icon name="x" size={13} /> {remove.isPending ? "Deleting..." : "Delete contract"}
          </button>
        </div>
      </div>
      <ErrorBox error={save.error || activate.error || remove.error || importYaml.error || conformanceQuery.error} />
      {saved && <div className="info-box">Contract saved.</div>}
      {activate.data && (
        <div className="info-box">
          Activated: {activate.data.created_checks.length} checks created, {activate.data.updated_checks.length} updated.
        </div>
      )}

      <div className="contract-modebar">
        {(["form", "yaml", "versions"] as const).map((m) => (
          <button key={m} className={`filter-chip${mode === m ? " on" : ""}`} onClick={() => setMode(m)}>
            {m === "form" ? "Editor" : m === "yaml" ? "ODCS YAML" : "Versions"}
          </button>
        ))}
      </div>

      {mode === "form" && (
        <div className="grid cols-2 contract-grid">
          <div className="card card-pad">
            <h3>Agreement</h3>
            <fieldset disabled={!editable} className="plain-fieldset">
              <div className="form-row">
                <label className="field">
                  Name
                  <input value={name} onChange={(e) => setName(e.target.value)} />
                </label>
                <label className="field">
                  Version
                  <input value={version} onChange={(e) => setVersion(e.target.value)} />
                </label>
              </div>
              <div className="form-row">
                <label className="field">
                  Owner
                  <input
                    value={draft.owner?.name ?? ""}
                    onChange={(e) => setDraft((s) => ({ ...s, owner: { ...(s.owner ?? {}), name: e.target.value } }))}
                  />
                </label>
                <label className="field">
                  Importance
                  <select
                    value={draft.owner?.importance ?? "medium"}
                    onChange={(e) =>
                      setDraft((s) => ({ ...s, owner: { ...(s.owner ?? {}), importance: e.target.value } }))
                    }
                  >
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                    <option value="critical">critical</option>
                  </select>
                </label>
              </div>
              <label className="field">
                Consumers
                <textarea
                  value={(draft.consumers ?? []).map((c) => c.name).join("\n")}
                  onChange={(e) =>
                    setDraft((s) => ({
                      ...s,
                      consumers: e.target.value.split("\n").map((name) => name.trim()).filter(Boolean).map((name) => ({ name })),
                    }))
                  }
                />
              </label>
              <label className="field">
                Terms
                <textarea value={draft.terms ?? ""} onChange={(e) => setDraft((s) => ({ ...s, terms: e.target.value }))} />
              </label>
            </fieldset>
          </div>

          <ConformancePanel
            conformance={conformance}
            materializedCheckIds={materializedCheckIds}
            isError={conformanceQuery.isError}
            error={conformanceQuery.error}
            onRetry={() => conformanceQuery.refetch()}
          />

          <div className="card card-pad">
            <div className="section-title compact">
              <h3>Schema</h3>
              {editable && <button className="small" onClick={addSourceColumns}>Add source columns</button>}
            </div>
            <fieldset disabled={!editable} className="plain-fieldset">
              <div className="contract-switches">
                <label><input type="checkbox" checked={draft.schema?.allow_extra_columns ?? true} onChange={(e) => setDraft((s) => ({ ...s, schema: { ...(s.schema ?? {}), allow_extra_columns: e.target.checked } }))} /> allow extra columns</label>
                <label><input type="checkbox" checked={draft.schema?.compare_types ?? false} onChange={(e) => setDraft((s) => ({ ...s, schema: { ...(s.schema ?? {}), compare_types: e.target.checked } }))} /> compare types</label>
                <label><input type="checkbox" checked={draft.schema?.enforce_nullable ?? false} onChange={(e) => setDraft((s) => ({ ...s, schema: { ...(s.schema ?? {}), enforce_nullable: e.target.checked } }))} /> enforce nullability</label>
              </div>
              <div className="contract-table-editor">
                {schemaColumns.map((col, i) => (
                  // Keyed by the row's own identity, never by `col.name` — that
                  // is what this row's first input edits (#283).
                  <div className="contract-column-row" key={col._rowId ?? `col-${i}`}>
                    <input value={col.name} onChange={(e) => updateColumn(i, { name: e.target.value })} placeholder="column" />
                    <input value={col.dtype ?? ""} onChange={(e) => updateColumn(i, { dtype: e.target.value })} placeholder="type" />
                    <label><input type="checkbox" checked={col.required ?? true} onChange={(e) => updateColumn(i, { required: e.target.checked })} /> required</label>
                    <label><input type="checkbox" checked={col.nullable ?? true} onChange={(e) => updateColumn(i, { nullable: e.target.checked })} /> nullable</label>
                    <button className="ghost small" onClick={() => removeColumn(i)} aria-label={`Remove column ${col.name || i + 1}`}><Icon name="x" size={13} /></button>
                  </div>
                ))}
              </div>
              <button className="small" onClick={() => setDraft((s) => ({ ...s, schema: { ...(s.schema ?? {}), columns: [...(s.schema?.columns ?? []), { name: "", dtype: "", required: true, nullable: true, _rowId: nextRowId() }] } }))}>
                <Icon name="plus" size={13} /> Add column
              </button>
            </fieldset>
          </div>

          <div className="card card-pad">
            <h3>Freshness & Volume</h3>
            <fieldset disabled={!editable} className="plain-fieldset">
              <div className="form-row">
                <label className="field">
                  Freshness column
                  <select value={draft.freshness?.column ?? ""} onChange={(e) => setDraft((s) => ({ ...s, freshness: { ...(s.freshness ?? {}), column: e.target.value } }))}>
                    <option value="">none</option>
                    {(columnsQuery.data ?? []).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                  </select>
                </label>
                <label className="field">
                  Max age hours
                  <input type="number" value={draft.freshness?.max_age_hours ?? ""} onChange={(e) => setDraft((s) => ({ ...s, freshness: { ...(s.freshness ?? {}), max_age_hours: e.target.value ? Number(e.target.value) : undefined } }))} />
                </label>
              </div>
              <div className="form-row">
                <label className="field">
                  Minimum rows
                  <input type="number" value={draft.volume?.min_rows ?? ""} onChange={(e) => setDraft((s) => ({ ...s, volume: { ...(s.volume ?? {}), min_rows: e.target.value ? Number(e.target.value) : undefined } }))} />
                </label>
                <label className="field">
                  Volume severity
                  <select value={draft.volume?.severity ?? "warn"} onChange={(e) => setDraft((s) => ({ ...s, volume: { ...(s.volume ?? {}), severity: e.target.value as Severity } }))}>
                    <option value="info">info</option>
                    <option value="warn">warn</option>
                    <option value="error">error</option>
                  </select>
                </label>
              </div>
            </fieldset>
          </div>

          <div className="card card-pad contract-wide">
            <div className="section-title compact">
              <h3>Quality clauses</h3>
              {editable && <button className="small" onClick={addQuality}><Icon name="plus" size={13} /> Add clause</button>}
            </div>
            <fieldset disabled={!editable} className="plain-fieldset">
              <div className="contract-quality-list">
                {quality.map((q, i) => {
                  const specs = checkTypes?.find((t) => t.key === q.check_type)?.params;
                  const paramErrors = validateParams(specs ?? [], q.params ?? {});
                  const missing = Object.keys(paramErrors);
                  const summary = paramsSummary(q.params);
                  const errId = `clause-params-err-${q._rowId ?? i}`;
                  return (
                    // Keyed by row identity: the clause `id` can repeat across
                    // imported clauses and the old `-${i}` suffix shifted every
                    // row below a removed one (#283).
                    <div className="contract-quality-row" key={q._rowId ?? `${q.id}-${i}`}>
                      <input value={q.name} onChange={(e) => updateQuality(i, { name: e.target.value })} placeholder="Name" />
                      <select value={q.check_type} onChange={(e) => updateQuality(i, { check_type: e.target.value })}>
                        <option value="not_null">not_null</option>
                        <option value="unique">unique</option>
                        <option value="accepted_values">accepted_values</option>
                        <option value="range">range</option>
                        <option value="regex_match">regex_match</option>
                        <option value="custom_sql">custom_sql</option>
                      </select>
                      <select value={q.column ?? ""} onChange={(e) => updateQuality(i, { column: e.target.value || null })}>
                        <option value="">table</option>
                        {(columnsQuery.data ?? []).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                      </select>
                      <select value={q.severity ?? "error"} onChange={(e) => updateQuality(i, { severity: e.target.value as Severity })}>
                        <option value="info">info</option>
                        <option value="warn">warn</option>
                        <option value="error">error</option>
                      </select>
                      {/* Typed param fields (shared with the Checks editor)
                          instead of hand-written JSON (#294). */}
                      <div style={{ minWidth: 0 }}>
                        <button
                          type="button"
                          className="small"
                          aria-label={`Edit parameters for ${q.name || q.check_type}`}
                          aria-describedby={missing.length ? errId : undefined}
                          title={summary || "No parameters set"}
                          style={{ width: "100%", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                          onClick={() => setParamsRowId(q._rowId ?? null)}
                        >
                          {summary || "Set parameters…"}
                        </button>
                        {missing.length > 0 && (
                          <span id={errId} className="field-error">
                            Needs {missing.join(", ")}
                          </span>
                        )}
                      </div>
                      <button className="ghost small" onClick={() => removeQuality(i)} aria-label={`Remove quality clause ${q.name || i + 1}`}><Icon name="x" size={13} /></button>
                    </div>
                  );
                })}
                {quality.length === 0 && <div className="muted">No quality clauses yet.</div>}
              </div>
            </fieldset>
          </div>
        </div>
      )}

      {mode === "form" && editable && editingClause && (
        <Modal
          title={`Parameters — ${editingClause.name || editingClause.check_type}`}
          onClose={() => setParamsRowId(null)}
          footer={
            <button className="primary" onClick={() => setParamsRowId(null)}>
              Done
            </button>
          }
        >
          {/* Fully controlled: every edit lands in the contract draft straight
              away, so closing this dialog can never discard analyst input. The
              contract itself is still saved with the Save button. */}
          <div className="field-hint" style={{ marginBottom: 10 }}>
            {editingType?.description ?? `Parameters for ${editingClause.check_type}.`}
          </div>
          {checkTypesQuery.isLoading && <Spinner label="Loading parameter schema..." />}
          <ErrorBox error={checkTypesQuery.error} />
          <CheckParamsForm
            specs={editingType?.params}
            params={editingClause.params ?? {}}
            onChange={(params) => updateQuality(editingIndex, { params })}
            errors={validateParams(editingType?.params ?? [], editingClause.params ?? {})}
          />
        </Modal>
      )}

      {mode === "yaml" && (
        <div className="grid cols-2">
          <div className="card card-pad">
            <h3>ODCS export</h3>
            <ErrorBox error={exportQuery.error} />
            {exportQuery.isLoading ? <Spinner /> : <textarea className="contract-yaml" value={exportQuery.data?.yaml ?? ""} readOnly />}
          </div>
          <div className="card card-pad">
            <h3>Import ODCS YAML</h3>
            <textarea className="contract-yaml" value={yamlText} onChange={(e) => setYamlText(e.target.value)} disabled={!editable} />
            {editable && <button onClick={() => importYaml.mutate()} disabled={importYaml.isPending || !yamlText.trim()}><Icon name="plus" size={13} /> Import as new contract</button>}
          </div>
        </div>
      )}

      {mode === "versions" && (
        <div className="grid cols-2">
          <div className="card card-pad">
            <h3>Version history</h3>
            <ErrorBox error={versionsQuery.error} />
            {versionsQuery.isLoading ? <Spinner /> : (
              <div className="dense-list">
                {versions.map((v) => (
                  <div className="dense-item" key={v.id}>
                    <div className="title">Snapshot #{v.id} · v{v.version}</div>
                    <div className="meta">{fmtDateTime(v.created_at)}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="card card-pad">
            <h3>Latest diff</h3>
            <ErrorBox error={diffQuery.error} />
            {versions.length < 2 ? <div className="muted">Save another version to see a diff.</div> : diffQuery.isLoading ? <Spinner /> : (
              <pre className="contract-diff">{[
                ...(diffQuery.data?.added ?? []).map((l) => `+ ${l}`),
                ...(diffQuery.data?.removed ?? []).map((l) => `- ${l}`),
              ].join("\n") || "No supported-field changes."}</pre>
            )}
          </div>
        </div>
      )}
    </div>
  );

  function updateColumn(index: number, patch: Partial<ContractColumn>) {
    setDraft((spec) => {
      const cols = [...(spec.schema?.columns ?? [])];
      // Spread-then-patch keeps `_rowId`, so the row's React key survives the edit.
      cols[index] = { ...cols[index], ...patch };
      return { ...spec, schema: { ...(spec.schema ?? {}), columns: cols } };
    });
  }

  function removeColumn(index: number) {
    setDraft((spec) => ({
      ...spec,
      schema: { ...(spec.schema ?? {}), columns: (spec.schema?.columns ?? []).filter((_, i) => i !== index) },
    }));
  }

  function addQuality() {
    setDraft((spec) => {
      const items = spec.quality ?? [];
      return {
        ...spec,
        quality: [
          ...items,
          {
            id: nextClauseId(items),
            _rowId: nextRowId(),
            name: "New clause",
            check_type: "not_null",
            severity: "error",
            params: {},
          },
        ],
      };
    });
  }

  function updateQuality(index: number, patch: Partial<QualityClause>) {
    setDraft((spec) => {
      const items = [...(spec.quality ?? [])];
      items[index] = { ...items[index], ...patch };
      return { ...spec, quality: items };
    });
  }

  function removeQuality(index: number) {
    // Drop the params dialog target first if it is the clause being removed
    // (side effects must not live inside a state updater).
    const target = (draft.quality ?? [])[index];
    if (target?._rowId && target._rowId === paramsRowId) setParamsRowId(null);
    setDraft((spec) => ({ ...spec, quality: (spec.quality ?? []).filter((_, i) => i !== index) }));
  }
}

function ConformancePanel({
  conformance,
  materializedCheckIds,
  isError,
  error,
  onRetry,
}: {
  conformance?: DataContractConformance;
  materializedCheckIds: Set<number>;
  isError?: boolean;
  error?: unknown;
  onRetry?: () => void;
}) {
  return (
    <div className="card card-pad">
      <div className="section-title compact">
        <h3>Conformance</h3>
        {conformance && <StatusPill value={conformance.status} />}
      </div>
      {isError && !conformance ? (
        // Don't spin forever when the conformance query fails (#D19).
        <div>
          <ErrorBox error={error} />
          {onRetry && (
            <button className="btn small" onClick={onRetry}>
              <Icon name="refresh" size={12} /> Retry
            </button>
          )}
        </div>
      ) : !conformance ? (
        <Spinner />
      ) : (
        <div className="contract-clause-list">
          {conformance.clauses.map((clause) => (
            <div key={clause.clause_id} className={`contract-clause ${clauseTone(clause.status)}`}>
              <div>
                <div className="contract-clause-title">{clauseLabel(clause)}</div>
                <div className="muted">{clause.detail}</div>
              </div>
              <div className="contract-clause-actions">
                <StatusPill value={clause.status} />
                {clause.check_id && materializedCheckIds.has(clause.check_id) && (
                  <Link to={`/datasets/${conformance.dataset_id}/checks`} className="btn small">
                    Check #{clause.check_id}
                  </Link>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
