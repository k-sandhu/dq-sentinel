import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { CustomDashboard, SqlWidget as SqlWidgetT } from "../../api/types";
import { timeAgo } from "../../lib/format";
import PanelChart from "../PanelChart";
import { ErrorBox } from "../ui";

/** Renders a sql widget's server-owned snapshot through the shared PanelChart
 *  (the ad-hoc dashboard viz contract — imported, not forked). Freshness is
 *  always labelled ("as of … (UTC)"); a snapshot error shows in a compact box;
 *  no snapshot yet shows an honest empty state plus a Refresh button for editors.
 *  Refresh re-runs every sql widget server-side and reloads the dashboard. */
export default function SqlWidget({
  widget,
  dashboardId,
  canRefresh,
}: {
  widget: SqlWidgetT;
  dashboardId: number;
  canRefresh: boolean;
}) {
  const qc = useQueryClient();
  const refresh = useMutation({
    mutationFn: () => api.post<CustomDashboard>(`/dashboards/custom/${dashboardId}/refresh`),
    onSuccess: (data) => {
      // refresh returns the whole dashboard with fresh snapshots — seed the cache
      qc.setQueryData(["custom-dashboard", dashboardId], data);
    },
  });

  const snap = widget.snapshot;

  const refreshButton = canRefresh ? (
    <button
      type="button"
      className="small ghost"
      onClick={() => refresh.mutate()}
      disabled={refresh.isPending}
    >
      {refresh.isPending ? "Refreshing…" : "Refresh"}
    </button>
  ) : null;

  if (!snap || !snap.refreshed_at) {
    return (
      <div className="cd-sql-empty">
        <div className="empty" style={{ padding: 10 }}>
          Not refreshed yet
        </div>
        {refreshButton}
        {refresh.isError && <ErrorBox error={refresh.error} />}
      </div>
    );
  }

  return (
    <div className="cd-sql">
      <div className="cd-sql-bar">
        <span className="cd-asof" title="Snapshots are captured server-side; data is as of the last refresh">
          as of {timeAgo(snap.refreshed_at)} (UTC)
        </span>
        {refreshButton}
      </div>
      {snap.error ? (
        <div className="error-box cd-sql-error">{snap.error}</div>
      ) : (
        <PanelChart columns={snap.columns} rows={snap.rows} viz={widget.config.viz} height={180} />
      )}
      {refresh.isError && <ErrorBox error={refresh.error} />}
    </div>
  );
}
