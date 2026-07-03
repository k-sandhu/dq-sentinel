import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import { qk } from "../api/queryKeys";
import type { Dataset, Profile } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { Breadcrumbs, ErrorBox, Icon, Spinner, StatusPill } from "../components/ui";
import { fmtNum, timeAgo } from "../lib/format";
import { isFavorite, pushRecent, subscribePrefs, toggleFavorite } from "../lib/prefs";
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
  const active: Tab = TABS.includes(tab as Tab) ? (tab as Tab) : "profile";
  // Tabs with in-progress form state report unsaved edits here so a tab switch can
  // warn first (BF-3 for Knowledge, #D4 for Contract — both hold a lot of typing).
  const knowledgeDirty = useRef(false);
  const contractDirty = useRef(false);

  const goTab = (t: Tab) => {
    const dirtyTab =
      active === "knowledge" && knowledgeDirty.current
        ? "knowledge"
        : active === "contract" && contractDirty.current
          ? "contract"
          : null;
    if (
      dirtyTab &&
      !window.confirm(`You have unsaved changes to this table's ${dirtyTab}. Leave without saving?`)
    )
      return;
    navigate(`/datasets/${datasetId}/${t}`);
  };

  const { data: dataset, error } = useQuery({
    queryKey: qk.datasets.detail(datasetId),
    queryFn: () => api.get<Dataset>(`/datasets/${datasetId}`),
  });

  const profileQuery = useQuery({
    queryKey: qk.profile.detail(datasetId),
    queryFn: () => api.get<Profile>(`/datasets/${datasetId}/profile`),
    retry: false, // 404 until first profiling
  });

  const runProfile = useMutation({
    mutationFn: () => api.post<Profile>(`/datasets/${datasetId}/profile`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.profile.detail(datasetId) });
      qc.invalidateQueries({ queryKey: qk.datasets.all });
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

  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!dataset) return <Spinner label="Loading dataset…" />;

  const datasetLabel = `${dataset.schema_name ? `${dataset.schema_name}.` : ""}${dataset.table_name}`;

  return (
    <div className="page">
      <Breadcrumbs items={[{ label: "Datasets", to: "/datasets" }, { label: datasetLabel }]} />
      <div className="page-header">
        <div>
          <h1>
            {dataset.schema_name ? `${dataset.schema_name}.` : ""}
            {dataset.table_name} <StatusPill value={dataset.health} />
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
        </div>
      </div>
      <ErrorBox error={runProfile.error} />

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
      {active === "contract" && <ContractTab dataset={dataset} dirtyRef={contractDirty} />}
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
      {active === "knowledge" && <KnowledgeTab datasetId={datasetId} dirtyRef={knowledgeDirty} />}
      {active === "rca" && <RcaTab datasetId={datasetId} />}
    </div>
  );
}
