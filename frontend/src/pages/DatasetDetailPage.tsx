import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, ApiError } from "../api/client";
import { qk } from "../api/queryKeys";
import type { Dataset, Profile } from "../api/types";
import { canEdit, isAdmin, useAuth } from "../auth";
import { useConfirm } from "../components/confirm";
import { DatasetHealth } from "../components/datasets/DatasetsTable";
import { Breadcrumbs, ErrorBox, Icon, NotFoundState, Spinner } from "../components/ui";
import { fmtNum, timeAgo } from "../lib/format";
import { isFavorite, pushRecent, subscribePrefs, toggleFavorite } from "../lib/prefs";
import { useUnsavedGuard } from "../lib/useUnsavedGuard";
import ChecksTab from "./dataset/ChecksTab";
import ContractTab from "./dataset/ContractTab";
import CodeTab from "./dataset/CodeTab";
import DashboardsTab from "./dataset/DashboardsTab";
import ExceptionsTab from "./dataset/ExceptionsTab";
import KnowledgeTab from "./dataset/KnowledgeTab";
import LineageTab from "./dataset/LineageTab";
import MonitorPackTab from "./dataset/MonitorPackTab";
import ProfileTab from "./dataset/ProfileTab";
import RcaTab from "./dataset/RcaTab";
import RunsTab from "./dataset/RunsTab";
import SchemaTab from "./dataset/SchemaTab";

const TABS = ["profile", "code", "schema", "lineage", "contract", "monitors", "checks", "runs", "exceptions", "dashboards", "knowledge", "rca"] as const;
type Tab = (typeof TABS)[number];

export default function DatasetDetailPage() {
  const { id, tab } = useParams();
  const datasetId = Number(id);
  const navigate = useNavigate();
  const { user } = useAuth();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const unregisterHintId = useId();
  const active: Tab = TABS.includes(tab as Tab) ? (tab as Tab) : "profile";

  // Tabs are routes, so the tabs that hold in-progress typing (Knowledge BF-3,
  // Contract #D4) guard this switch themselves via useUnsavedGuard — the same
  // shared dialog that now also covers sidebar links and global search (#284).
  // This page no longer needs its own dirty bookkeeping or a native confirm().
  const goTab = (t: Tab) => navigate(`/datasets/${datasetId}/${t}`);

  // This page holds no typing of its own (never blocks), but it needs the guard's
  // bypass handle: those tab-level guards patch the shared router navigator, so
  // *any* navigate() from here is intercepted while a tab is dirty — including the
  // redirect after the dataset is deleted (see `unregister` below).
  const guard = useUnsavedGuard(false);

  // A mistyped link ("/datasets/not-a-number") must land on the designed
  // not-found, not a 422 error box — and must not fire /datasets/NaN requests.
  const validId = Number.isInteger(datasetId) && datasetId > 0;

  const { data: dataset, error } = useQuery({
    queryKey: qk.datasets.detail(datasetId),
    queryFn: () => api.get<Dataset>(`/datasets/${datasetId}`),
    enabled: validId,
  });

  const profileQuery = useQuery({
    queryKey: qk.profile.detail(datasetId),
    queryFn: () => api.get<Profile>(`/datasets/${datasetId}/profile`),
    retry: false, // 404 until first profiling
    enabled: validId,
  });

  const runProfile = useMutation({
    mutationFn: () => api.post<Profile>(`/datasets/${datasetId}/profile`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.profile.detail(datasetId) });
      qc.invalidateQueries({ queryKey: qk.datasets.all });
    },
  });

  // Unregister (#289): DELETE /datasets/{id} was wired but unreachable from the UI,
  // so a mis-registered table polluted health rollups and coverage % forever unless
  // an admin deleted the whole connection. Admin-only, type-to-confirm, and the
  // cascade is spelled out honestly in the dialog (see core/deletion.py).
  const unregister = useMutation({
    mutationFn: () => api.del<void>(`/datasets/${datasetId}`),
    onSuccess: () => {
      // Drop this dataset's own cache entries FIRST so the list invalidation below
      // can't refetch a row that no longer exists (a 404 flash on the way out).
      qc.removeQueries({ queryKey: qk.datasets.detail(datasetId) });
      qc.removeQueries({ queryKey: qk.profile.detail(datasetId) });
      // The dataset is gone: a dirty Contract/Knowledge tab must not get to ask
      // "keep editing?" here — "Keep editing" would strand the analyst on the
      // not-found state of a dataset that no longer exists (same reasoning as
      // CustomDashboardPage's post-delete redirect).
      guard.bypass(() => navigate("/datasets", { replace: true }));
      // Rollups that counted this dataset must recompute immediately.
      qc.invalidateQueries({ queryKey: qk.datasets.all });
      qc.invalidateQueries({ queryKey: qk.dashboard.all });
      qc.invalidateQueries({ queryKey: qk.dashboardConsole.all });
      qc.invalidateQueries({ queryKey: qk.scorecards.all });
      qc.invalidateQueries({ queryKey: qk.reliability.all });
      qc.invalidateQueries({ queryKey: qk.catalog.all });
      qc.invalidateQueries({ queryKey: qk.checks.all });
      qc.invalidateQueries({ queryKey: qk.savedQueries.all });
    },
  });

  // Recently-viewed (#59): record this visit (dedupe + cap handled in prefs).
  useEffect(() => {
    if (Number.isFinite(datasetId)) pushRecent(datasetId);
  }, [datasetId]);

  // Favorite toggle state, kept in sync with the sidebar / datasets page.
  const [fav, setFav] = useState(() => isFavorite(datasetId));
  useEffect(() => setFav(isFavorite(datasetId)), [datasetId]);
  useEffect(() => subscribePrefs(() => setFav(isFavorite(datasetId))), [datasetId]);

  if (!validId || (error instanceof ApiError && error.status === 404))
    return <NotFoundState what="Dataset" backTo="/datasets" backLabel="Back to datasets" />;
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!dataset) return <Spinner label="Loading dataset…" />;

  const datasetLabel = `${dataset.schema_name ? `${dataset.schema_name}.` : ""}${dataset.table_name}`;

  return (
    <div className="page">
      <Breadcrumbs items={[{ label: "Datasets", to: "/datasets" }, { label: datasetLabel }]} />
      <div className="page-header">
        <div>
          {/* Same component as the datasets list row (#262): a dataset whose checks
              error reads as REPAIR with a link to the errored run, not as a bare red
              verdict over an empty Exceptions tab. Sharing it is what stops the header
              contradicting the row the analyst just clicked. */}
          <h1>
            {dataset.schema_name ? `${dataset.schema_name}.` : ""}
            {dataset.table_name} <DatasetHealth d={dataset} />
          </h1>
          <div className="sub">
            {dataset.connection_name} · {fmtNum(dataset.row_count)} rows · profiled {timeAgo(dataset.last_profiled_at)} ·{" "}
            {dataset.active_checks} active checks · {dataset.open_exceptions} open exceptions
          </div>
        </div>
        <div className="header-actions">
          <button
            type="button"
            className={`icon-only star-btn${fav ? " on" : ""}`}
            aria-pressed={fav}
            aria-label={fav ? "Remove from favorites" : "Add to favorites"}
            title={fav ? "Remove from favorites" : "Add to favorites"}
            onClick={() => {
              setFav(toggleFavorite(datasetId)); // optimistic; dq:prefs keeps siblings in sync
            }}
          >
            <Icon name={fav ? "star-filled" : "star"} size={15} />
          </button>
          <Link to={`/workbench?dataset_id=${datasetId}`} className="btn">
            <Icon name="search" size={13} /> Workbench
          </Link>
          {canEdit(user) && (
            <button onClick={() => runProfile.mutate()} disabled={runProfile.isPending}>
              {runProfile.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="refresh" size={14} />}
              {runProfile.isPending ? "Profiling…" : "Profile now"}
            </button>
          )}
          {/* Admin-only (the endpoint gates on the global admin role). The reason is
              visible text, not a title on a disabled control, so it reaches keyboard
              and screen-reader users too. */}
          <button
            type="button"
            className="danger"
            disabled={!isAdmin(user) || unregister.isPending}
            aria-describedby={isAdmin(user) ? undefined : unregisterHintId}
            title={isAdmin(user) ? "Remove this dataset from DQ Sentinel (the source table is not touched)" : undefined}
            onClick={async () => {
              if (
                await confirm({
                  title: "Unregister dataset",
                  danger: true,
                  confirmLabel: "Unregister dataset",
                  typeToConfirm: dataset.table_name,
                  body: (
                    <>
                      <p style={{ margin: "0 0 8px" }}>
                        This removes <strong>{datasetLabel}</strong> from DQ Sentinel. The table in{" "}
                        <strong>{dataset.connection_name}</strong> is <strong>not</strong> touched — DQ
                        Sentinel only ever reads from your sources.
                      </p>
                      <p style={{ margin: "0 0 4px" }}>Permanently deleted with it:</p>
                      <ul style={{ margin: "0 0 8px", paddingLeft: 18 }}>
                        <li>
                          all {dataset.active_checks} active check(s) and every archived check on this
                          dataset, plus their entire run history
                        </li>
                        <li>
                          all exceptions raised on it ({dataset.open_exceptions} currently open) and their
                          triage history
                        </li>
                        <li>profiles, schema-history snapshots and any pinned schema baseline</li>
                        <li>root-cause analyses, incidents and their timelines</li>
                        <li>ad-hoc dashboards, notification rules, and SLAs scoped to it or its checks</li>
                        <li>its dataset-level scorecard history</li>
                      </ul>
                      <p style={{ margin: 0 }}>
                        Saved workbench queries pinned here are kept — they just lose the pin. Re-registering
                        the table later starts from an empty history. This cannot be undone.
                      </p>
                    </>
                  ),
                })
              )
                unregister.mutate();
            }}
          >
            <Icon name="x" size={13} /> {unregister.isPending ? "Unregistering…" : "Unregister"}
          </button>
          {!isAdmin(user) && (
            <span id={unregisterHintId} className="badge">admin only</span>
          )}
        </div>
      </div>
      <ErrorBox error={runProfile.error || unregister.error} />

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={`tab${active === t ? " on" : ""}`} onClick={() => goTab(t)}>
            {t === "rca" ? "Root cause" : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {active === "profile" && (
        <ProfileTab
          datasetId={datasetId}
          profile={profileQuery.data ?? null}
          loading={profileQuery.isLoading}
          onProfileNow={canEdit(user) ? () => runProfile.mutate() : undefined}
          profiling={runProfile.isPending}
        />
      )}
      {active === "code" && <CodeTab datasetId={datasetId} />}
      {active === "schema" && <SchemaTab datasetId={datasetId} />}
      {active === "lineage" && <LineageTab dataset={dataset} />}
      {active === "contract" && <ContractTab dataset={dataset} />}
      {active === "monitors" && (
        <MonitorPackTab
          datasetId={datasetId}
          hasProfile={!!profileQuery.data}
          onProfileNow={canEdit(user) ? () => runProfile.mutate() : undefined}
          profiling={runProfile.isPending}
        />
      )}
      {active === "checks" && <ChecksTab datasetId={datasetId} hasProfile={!!profileQuery.data} />}
      {active === "runs" && <RunsTab datasetId={datasetId} />}
      {active === "exceptions" && <ExceptionsTab datasetId={datasetId} />}
      {active === "dashboards" && <DashboardsTab datasetId={datasetId} hasProfile={!!profileQuery.data} />}
      {active === "knowledge" && <KnowledgeTab datasetId={datasetId} />}
      {active === "rca" && <RcaTab datasetId={datasetId} />}
    </div>
  );
}
