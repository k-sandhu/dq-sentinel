import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import type { Dataset, Profile } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { ErrorBox, Icon, Pill, Spinner } from "../components/ui";
import { fmtNum, timeAgo } from "../lib/format";
import ChecksTab from "./dataset/ChecksTab";
import CodeTab from "./dataset/CodeTab";
import DashboardsTab from "./dataset/DashboardsTab";
import ExceptionsTab from "./dataset/ExceptionsTab";
import KnowledgeTab from "./dataset/KnowledgeTab";
import LineageTab from "./dataset/LineageTab";
import ProfileTab from "./dataset/ProfileTab";
import RcaTab from "./dataset/RcaTab";
import RunsTab from "./dataset/RunsTab";

const TABS = ["profile", "code", "lineage", "checks", "runs", "exceptions", "dashboards", "knowledge", "rca"] as const;
type Tab = (typeof TABS)[number];

export default function DatasetDetailPage() {
  const { id, tab } = useParams();
  const datasetId = Number(id);
  const navigate = useNavigate();
  const { user } = useAuth();
  const qc = useQueryClient();
  const active: Tab = TABS.includes(tab as Tab) ? (tab as Tab) : "profile";

  const { data: dataset, error } = useQuery({
    queryKey: ["datasets", datasetId],
    queryFn: () => api.get<Dataset>(`/datasets/${datasetId}`),
  });

  const profileQuery = useQuery({
    queryKey: ["profile", datasetId],
    queryFn: () => api.get<Profile>(`/datasets/${datasetId}/profile`),
    retry: false, // 404 until first profiling
  });

  const runProfile = useMutation({
    mutationFn: () => api.post<Profile>(`/datasets/${datasetId}/profile`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profile", datasetId] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
  });

  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!dataset) return <Spinner label="Loading dataset…" />;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>
            {dataset.schema_name ? `${dataset.schema_name}.` : ""}
            {dataset.table_name} <Pill value={dataset.health} />
          </h1>
          <div className="sub">
            {dataset.connection_name} · {fmtNum(dataset.row_count)} rows · profiled {timeAgo(dataset.last_profiled_at)} ·{" "}
            {dataset.active_checks} active checks · {dataset.open_exceptions} open exceptions
          </div>
        </div>
        <div className="header-actions">
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
          <button key={t} className={`tab${active === t ? " on" : ""}`} onClick={() => navigate(`/datasets/${datasetId}/${t}`)}>
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
      {active === "lineage" && <LineageTab dataset={dataset} />}
      {active === "checks" && <ChecksTab datasetId={datasetId} hasProfile={!!profileQuery.data} />}
      {active === "runs" && <RunsTab datasetId={datasetId} />}
      {active === "exceptions" && <ExceptionsTab datasetId={datasetId} />}
      {active === "dashboards" && <DashboardsTab datasetId={datasetId} hasProfile={!!profileQuery.data} />}
      {active === "knowledge" && <KnowledgeTab datasetId={datasetId} />}
      {active === "rca" && <RcaTab datasetId={datasetId} />}
    </div>
  );
}
