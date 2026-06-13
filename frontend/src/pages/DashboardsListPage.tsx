import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { CustomDashboard, CustomDashboardMeta } from "../api/types";
import { EmptyState, ErrorBox, Icon, Spinner } from "../components/ui";
import { timeAgo } from "../lib/format";

/** Landing list for custom dashboards: mine + team boards as a card grid, plus a
 *  "New dashboard" action that creates an empty private board and opens it in
 *  builder mode. */
export default function DashboardsListPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["custom-dashboards"],
    queryFn: () => api.get<CustomDashboardMeta[]>("/dashboards/custom"),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<CustomDashboard>("/dashboards/custom", {
        name: "Untitled dashboard",
        description: "",
        visibility: "private",
        layout: { version: 1, widgets: [] },
      }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["custom-dashboards"] });
      navigate(`/dashboards/${d.id}?edit=1`);
    },
  });

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Dashboards</h1>
          <div className="sub">
            Compose your own glanceable screen — your datasets, your severity mix, your numbers.
            Share with the team or set one as your landing page.
          </div>
        </div>
        <div className="header-actions">
          <button className="primary" onClick={() => create.mutate()} disabled={create.isPending}>
            <Icon name="plus" size={14} /> New dashboard
          </button>
        </div>
      </div>

      {create.isError && <ErrorBox error={create.error} />}
      {error && <ErrorBox error={error} />}
      {isLoading && <Spinner label="Loading dashboards…" />}

      {data && data.length === 0 && (
        <EmptyState title="No dashboards yet" hint="Create one to assemble your morning screen.">
          <button className="primary" onClick={() => create.mutate()} disabled={create.isPending}>
            <Icon name="plus" size={14} /> New dashboard
          </button>
        </EmptyState>
      )}

      {data && data.length > 0 && (
        <div className="cd-card-grid">
          {data.map((d) => (
            <button
              type="button"
              key={d.id}
              className="card cd-card"
              onClick={() => navigate(`/dashboards/${d.id}`)}
            >
              <div className="cd-card-head">
                <span className="cd-card-name">{d.name}</span>
                <span className={`pill ${d.visibility === "team" ? "ok" : "unknown"}`}>{d.visibility}</span>
              </div>
              {d.description && <div className="cd-card-desc">{d.description}</div>}
              <div className="cd-card-foot">
                <span title={d.owner_active ? d.owner_name : `${d.owner_name} (inactive)`}>
                  {d.owner_name}
                  {!d.owner_active && " (inactive)"}
                </span>
                <span>·</span>
                <span>
                  {d.widget_count} widget{d.widget_count === 1 ? "" : "s"}
                </span>
                <span>·</span>
                <span>updated {timeAgo(d.updated_at)}</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
