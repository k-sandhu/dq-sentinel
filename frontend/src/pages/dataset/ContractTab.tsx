import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { api, ApiError } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type {
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
import { ErrorBox, Icon, Spinner, StatusPill } from "../../components/ui";

interface ContractColumn {
  name: string;
  dtype?: string;
  nullable?: boolean;
  required?: boolean;
  description?: string;
}

interface QualityClause {
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

function normalizeSpec(spec: ContractSpec): ContractSpec {
  return {
    version: spec.version ?? 1,
    schema: {
      columns: spec.schema?.columns ?? [],
      allow_extra_columns: spec.schema?.allow_extra_columns ?? true,
      compare_types: spec.schema?.compare_types ?? false,
      enforce_nullable: spec.schema?.enforce_nullable ?? false,
    },
    freshness: spec.freshness ?? {},
    volume: spec.volume ?? {},
    quality: spec.quality ?? [],
    owner: spec.owner ?? {},
    consumers: spec.consumers ?? [],
    terms: spec.terms ?? "",
    ...(spec.materialized !== undefined ? { materialized: spec.materialized } : {}),
  };
}

function clauseTone(status: string) {
  return status === "pass" ? "ok" : status === "breached" ? "danger" : "neutral";
}

function clauseLabel(clause: ContractClauseConformance) {
  return `${clause.kind}: ${clause.label}`;
}

export default function ContractTab({ dataset }: { dataset: Dataset }) {
  const { user } = useAuth();
  const editable = canEdit(user);
  const qc = useQueryClient();
  const [mode, setMode] = useState<"form" | "yaml" | "versions">("form");
  const [name, setName] = useState("");
  const [version, setVersion] = useState("0.1.0");
  const [draft, setDraft] = useState<ContractSpec>(normalizeSpec({}));
  const [yamlText, setYamlText] = useState("");
  const [saved, setSaved] = useState(false);

  const contractQuery = useQuery({
    queryKey: qk.contract.detail(dataset.id),
    queryFn: () => api.get<DataContract>(`/datasets/${dataset.id}/contract`),
    retry: (count, error) => error instanceof ApiError && error.status === 404 ? false : count < 2,
  });
  const contract = contractQuery.data;

  const columnsQuery = useQuery({
    queryKey: qk.columns.detail(dataset.id),
    queryFn: () => api.get<ColumnInfo[]>(`/datasets/${dataset.id}/columns`),
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
    setDraft(normalizeSpec(asSpec(contract.spec)));
  }, [contract]);

  useEffect(() => {
    if (exportQuery.data?.yaml && !yamlText) setYamlText(exportQuery.data.yaml);
  }, [exportQuery.data, yamlText]);

  const create = useMutation({
    mutationFn: () => api.post<DataContract>(`/datasets/${dataset.id}/contract`, {}),
    onSuccess: (created) => {
      qc.setQueryData(["contract", dataset.id], created);
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
      qc.setQueryData(["contract", dataset.id], updated);
      qc.invalidateQueries({ queryKey: qk.contractVersions.detail(dataset.id, updated.id) });
      setSaved(true);
      setTimeout(() => setSaved(false), 2200);
    },
  });

  const activate = useMutation({
    mutationFn: () => api.post<DataContractApplyResult>(`/datasets/${dataset.id}/contract/${contract!.id}/activate`),
    onSuccess: (result) => {
      qc.setQueryData(["contract", dataset.id], result.contract);
      qc.invalidateQueries({ queryKey: qk.checks.all });
      qc.invalidateQueries({ queryKey: qk.contractConformance.detail(dataset.id, result.contract.id) });
      qc.invalidateQueries({ queryKey: qk.contractVersions.detail(dataset.id, result.contract.id) });
    },
  });

  const importYaml = useMutation({
    mutationFn: () => api.post<DataContract>(`/datasets/${dataset.id}/contract/import`, { yaml: yamlText }),
    onSuccess: (created) => {
      qc.setQueryData(["contract", dataset.id], created);
      qc.invalidateQueries({ queryKey: qk.contract.all });
      setMode("form");
    },
  });

  const addSourceColumns = () => {
    const existing = new Set((draft.schema?.columns ?? []).map((c) => c.name.toLowerCase()));
    const additions = (columnsQuery.data ?? [])
      .filter((c) => !existing.has(c.name.toLowerCase()))
      .map((c) => ({ name: c.name, dtype: c.dtype, nullable: c.nullable, required: true }));
    setDraft((spec) => ({
      ...spec,
      schema: { ...(spec.schema ?? {}), columns: [...(spec.schema?.columns ?? []), ...additions] },
    }));
  };

  const quality = draft.quality ?? [];
  const schemaColumns = draft.schema?.columns ?? [];
  const conformance = conformanceQuery.data;

  const materializedCheckIds = useMemo(() => {
    const mat = (draft.materialized ?? {}) as { checks?: { check_id?: number }[] };
    return new Set((mat.checks ?? []).map((c) => c.check_id).filter((id): id is number => typeof id === "number"));
  }, [draft.materialized]);

  if (contractQuery.isLoading) return <Spinner label="Loading contract..." />;
  if (!contract && contractQuery.error instanceof ApiError && contractQuery.error.status === 404) {
    return (
      <div className="contract-empty">
        <div className="card card-pad">
          <h3>No data contract yet</h3>
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
        </div>
      </div>
      <ErrorBox error={save.error || activate.error || importYaml.error || conformanceQuery.error} />
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

          <ConformancePanel conformance={conformance} materializedCheckIds={materializedCheckIds} />

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
                  <div className="contract-column-row" key={`${col.name}-${i}`}>
                    <input value={col.name} onChange={(e) => updateColumn(i, { name: e.target.value })} placeholder="column" />
                    <input value={col.dtype ?? ""} onChange={(e) => updateColumn(i, { dtype: e.target.value })} placeholder="type" />
                    <label><input type="checkbox" checked={col.required ?? true} onChange={(e) => updateColumn(i, { required: e.target.checked })} /> required</label>
                    <label><input type="checkbox" checked={col.nullable ?? true} onChange={(e) => updateColumn(i, { nullable: e.target.checked })} /> nullable</label>
                    <button className="ghost small" onClick={() => removeColumn(i)} aria-label="Remove column"><Icon name="x" size={13} /></button>
                  </div>
                ))}
              </div>
              <button className="small" onClick={() => setDraft((s) => ({ ...s, schema: { ...(s.schema ?? {}), columns: [...(s.schema?.columns ?? []), { name: "", dtype: "", required: true, nullable: true }] } }))}>
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
                {quality.map((q, i) => (
                  <div className="contract-quality-row" key={`${q.id}-${i}`}>
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
                    <input value={JSON.stringify(q.params ?? {})} onChange={(e) => updateQualityParams(i, e.target.value)} placeholder='{"values":[]}' />
                    <button className="ghost small" onClick={() => removeQuality(i)} aria-label="Remove quality clause"><Icon name="x" size={13} /></button>
                  </div>
                ))}
                {quality.length === 0 && <div className="muted">No quality clauses yet.</div>}
              </div>
            </fieldset>
          </div>
        </div>
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
    setDraft((spec) => ({
      ...spec,
      quality: [
        ...(spec.quality ?? []),
        { id: `quality-${(spec.quality ?? []).length + 1}`, name: "New clause", check_type: "not_null", severity: "error", params: {} },
      ],
    }));
  }

  function updateQuality(index: number, patch: Partial<QualityClause>) {
    setDraft((spec) => {
      const items = [...(spec.quality ?? [])];
      items[index] = { ...items[index], ...patch };
      return { ...spec, quality: items };
    });
  }

  function updateQualityParams(index: number, raw: string) {
    try {
      updateQuality(index, { params: JSON.parse(raw || "{}") as Record<string, unknown> });
    } catch {
      updateQuality(index, { rationale: "Invalid params JSON" });
    }
  }

  function removeQuality(index: number) {
    setDraft((spec) => ({ ...spec, quality: (spec.quality ?? []).filter((_, i) => i !== index) }));
  }
}

function ConformancePanel({
  conformance,
  materializedCheckIds,
}: {
  conformance?: DataContractConformance;
  materializedCheckIds: Set<number>;
}) {
  return (
    <div className="card card-pad">
      <div className="section-title compact">
        <h3>Conformance</h3>
        {conformance && <StatusPill value={conformance.status} />}
      </div>
      {!conformance ? (
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
