import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Check, CheckTypeInfo, ColumnInfo, GenerateResult, Health } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import ChecksTable, { invalidateCheckCaches } from "../../components/ChecksTable";
import { CheckViewSwitch, useCheckView } from "../../components/CheckViews";
import CheckParamsForm, { validateParams } from "../../components/CheckParamsForm";
import { EmptyState, ErrorBox, Icon, Modal, Spinner } from "../../components/ui";

function NewCheckModal({ datasetId, onClose }: { datasetId: number; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: types } = useQuery({
    queryKey: qk.checkTypes.list(),
    queryFn: () => api.get<CheckTypeInfo[]>("/checks/types"),
  });
  const { data: columns } = useQuery({
    queryKey: qk.columns.detail(datasetId),
    queryFn: () => api.get<ColumnInfo[]>(`/datasets/${datasetId}/columns`),
  });

  const [checkType, setCheckType] = useState("not_null");
  const [column, setColumn] = useState("");
  const [severity, setSeverity] = useState("error");
  const [params, setParams] = useState<Record<string, unknown>>({});
  // The type hint sits inside the <label>, so an implicit association folds the
  // *selected type's description* into the select's accessible name — a name
  // that then changes on every selection ("Check type Column must never be
  // null" → "Check type Values within bounds"). Name from the caption span and
  // leave the description a description, as the Knowledge tab does (#260).
  const uid = useId();
  const id = { type: `${uid}-type`, typeCaption: `${uid}-type-caption`, typeHint: `${uid}-type-hint` };

  const selected = types?.find((t) => t.key === checkType);
  const paramErrors = validateParams(selected?.params ?? [], params);
  const hasParamErrors = Object.keys(paramErrors).length > 0;
  const dirty = checkType !== "not_null" || column !== "" || severity !== "error" || Object.keys(params).length > 0;

  const create = useMutation({
    mutationFn: () =>
      api.post<Check>("/checks", {
        dataset_id: datasetId,
        check_type: checkType,
        column_name: selected?.needs_column ? column : null,
        severity,
        params,
        status: "active",
      }),
    onSuccess: () => {
      // Created active: the dataset header's "N active checks" and the datasets
      // index rollups move with it, so use the shared check-mutation set (#285).
      invalidateCheckCaches(qc);
      onClose();
    },
  });

  return (
    <Modal
      title="New check"
      onClose={onClose}
      dirty={dirty}
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            onClick={() => create.mutate()}
            disabled={create.isPending || (selected?.needs_column && !column) || hasParamErrors}
          >
            Create & activate
          </button>
        </>
      }
    >
      <ErrorBox error={create.error} />
      <label className="field" htmlFor={id.type}>
        <span id={id.typeCaption}>Check type</span>
        <select
          id={id.type}
          aria-labelledby={id.typeCaption}
          aria-describedby={id.typeHint}
          value={checkType}
          onChange={(e) => { setCheckType(e.target.value); setParams({}); }}
        >
          {types?.map((t) => (
            <option key={t.key} value={t.key}>{t.label}</option>
          ))}
        </select>
        <div className="field-hint" id={id.typeHint}>{selected?.description}</div>
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
      <div className="field-group-label">Parameters</div>
      <CheckParamsForm specs={selected?.params} params={params} onChange={setParams} errors={paramErrors} />
    </Modal>
  );
}

export default function ChecksTab({ datasetId, hasProfile }: { datasetId: number; hasProfile: boolean }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const { view, setView } = useCheckView();
  const location = useLocation();
  const mounted = useRef(false);
  const currentRoute = useRef({ datasetId, pathname: location.pathname, setView });
  currentRoute.current = { datasetId, pathname: location.pathname, setView };
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get("q") ?? "";
  const status = ["active", "disabled"].includes(searchParams.get("status") ?? "") ? searchParams.get("status")! : "all";
  const lastStatuses = searchParams.getAll("last_status").filter(Boolean);
  const patchFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value); else next.delete(key);
    setSearchParams(next, { replace: key === "q" });
  };
  const [explore, setExplore] = useState(false);
  const [genResult, setGenResult] = useState<GenerateResult | null>(null);
  useEffect(() => { setGenResult(null); }, [datasetId]);

  const { data: health } = useQuery({ queryKey: qk.health.get(), queryFn: () => api.get<Health>("/health") });
  const llm = health?.llm_enabled ?? false;

  const { data: checks, isLoading, error } = useQuery({
    queryKey: qk.checks.byDatasetObj(datasetId),
    queryFn: () => api.get<Check[]>(`/checks?dataset_id=${datasetId}`),
  });

  const generate = useMutation({
    mutationFn: (origin: { datasetId: number; pathname: string }) =>
      api.post<GenerateResult>("/checks/generate", { dataset_id: origin.datasetId, use_llm: llm, explore: llm && explore }),
    onSuccess: (result, origin) => {
      // Generation writes new `proposed` rows, which the home dashboard counts
      // as `proposed_checks` — same shared set, plus this tab's explorer log.
      invalidateCheckCaches(qc);
      qc.invalidateQueries({ queryKey: qk.exploration.detail(origin.datasetId) });
      // Mutation callbacks can outlive this tab and can run after the same
      // component starts showing another dataset. Refresh shared data either
      // way, but only move the originating, still-visible tab into review.
      const current = currentRoute.current;
      if (mounted.current && current.datasetId === origin.datasetId && current.pathname === origin.pathname) {
        setGenResult(result);
        // Read the latest setter so URL params changed during generation survive.
        current.setView("proposals", true);
      }
    },
  });

  const proposals = (checks ?? []).filter((c) => c.status === "proposed");
  const shown = (checks ?? []).filter((c) => (
    view === "proposals" ? c.status === "proposed"
      : c.status !== "proposed" && c.status !== "archived" && (status === "all" || c.status === status)
        && (lastStatuses.length === 0 || lastStatuses.includes(c.last_status ?? "unknown"))
  ) && (!search || `${c.name} ${c.column_name ?? ""} ${c.check_type}`.toLowerCase().includes(search.toLowerCase())));

  return (
    <div>
      <CheckViewSwitch view={view} proposals={proposals.length} onChange={setView} />
      <p className="check-scope">{view === "results"
        ? "Latest recorded results, grouped by outcome and column. Includes paused checks and checks that have never run. Archived checks are excluded."
        : "Review proposals before activating. They do not run until activated. Bulk actions apply to shown proposals."}</p>
      <div className="toolbar">
        <input type="search" aria-label="Search checks" placeholder="Search checks or columns…" value={search} onChange={(e) => patchFilter("q", e.target.value)} style={{ maxWidth: 320, marginTop: 0 }} />
        {view === "results" && <div className="chip-row" role="group" aria-label="Check status">
          {([ ["all", "All current checks"], ["active", "Active"], ["disabled", "Paused"] ] as const).map(([value, label]) => <button key={value} type="button" className={`filter-chip${status === value ? " on" : ""}`} aria-pressed={status === value} onClick={() => patchFilter("status", value === "all" ? "" : value)}>{label}</button>)}
        </div>}
        {view === "results" && lastStatuses.length > 0 && <span className="filter-tag">latest result {lastStatuses.join(" / ")}<button type="button" className="tag-x" aria-label="Clear latest result filter" onClick={() => patchFilter("last_status", "")}>&times;</button></span>}
      </div>
      {canEdit(user) && (
        <div className="toolbar">
          <button className="primary" onClick={() => generate.mutate({ datasetId, pathname: location.pathname })} disabled={generate.isPending || !hasProfile} title={!hasProfile ? "Profile the dataset first" : undefined}>
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
        <div className="info-box" role="status">
          Generated <strong>{genResult.created}</strong> proposal{genResult.created === 1 ? "" : "s"} via{" "}
          <strong>{genResult.mode === "llm" ? "AI" : "heuristics"}</strong>
          {genResult.explored ? " after exploring the data" : ""}
          {genResult.skipped_duplicates ? ` (${genResult.skipped_duplicates} duplicates skipped)` : ""}. Review and activate them in Proposals.
        </div>
      )}
      {isLoading ? <Spinner /> : shown.length > 0 ? <ChecksTable checks={shown} showDataset={false} /> : <div className="card"><EmptyState
        title={search || (view === "results" && (status !== "all" || lastStatuses.length > 0)) ? "No checks match your filters" : view === "proposals" ? "No proposals to review" : "No operational checks yet"}
        hint={search || (view === "results" && (status !== "all" || lastStatuses.length > 0)) ? "Clear the search or status filters to see more checks." : view === "proposals" ? "Generate proposals from a profile to review them here." : "Review and activate proposals, or add a new check."}
      /></div>}
      {creating && <NewCheckModal datasetId={datasetId} onClose={() => setCreating(false)} />}
    </div>
  );
}
