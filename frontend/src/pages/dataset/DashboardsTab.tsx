import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { AdhocDashboard, AdhocDashboardMeta, Health, Panel } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import PanelChart from "../../components/PanelChart";
import { EmptyState, ErrorBox, Icon, Spinner } from "../../components/ui";
import { fmtDateTime } from "../../lib/format";

function PanelCard({ panel }: { panel: Panel }) {
  const [showSql, setShowSql] = useState(false);
  const isNumber = panel.viz.type === "number";
  return (
    <div className="card card-pad" style={isNumber ? { gridColumn: "span 1" } : { gridColumn: "span 2" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <h3 style={{ marginBottom: 2 }}>{panel.title}</h3>
        <button className="ghost small" title="Show SQL" onClick={() => setShowSql(!showSql)}>
          <Icon name="book" size={12} />
        </button>
      </div>
      {panel.description && (
        <div style={{ fontSize: 11.5, color: "var(--text-light)", marginBottom: 6 }}>{panel.description}</div>
      )}
      {showSql && <pre className="result" style={{ fontSize: 11 }}>{panel.sql}</pre>}
      {panel.error ? (
        <div className="error-box">{panel.error}</div>
      ) : (
        <PanelChart columns={panel.columns} rows={panel.rows} viz={panel.viz} height={isNumber ? 60 : 210} />
      )}
      <div style={{ fontSize: 10.5, color: "var(--text-light)", marginTop: 4 }}>{panel.elapsed_ms} ms</div>
    </div>
  );
}

export default function DashboardsTab({ datasetId, hasProfile }: { datasetId: number; hasProfile: boolean }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [focus, setFocus] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);

  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const llm = health?.llm_enabled ?? false;

  const metas = useQuery({
    queryKey: ["adhoc", { datasetId }],
    queryFn: () => api.get<AdhocDashboardMeta[]>(`/adhoc-dashboards?dataset_id=${datasetId}`),
  });

  const dashboard = useQuery({
    queryKey: ["adhoc-open", openId],
    queryFn: () => api.get<AdhocDashboard>(`/adhoc-dashboards/${openId}`),
    enabled: !!openId,
  });

  const generate = useMutation({
    mutationFn: () => api.post<AdhocDashboard>("/adhoc-dashboards/generate", { dataset_id: datasetId, focus }),
    onSuccess: (d) => {
      setFocus("");
      qc.invalidateQueries({ queryKey: ["adhoc"] });
      qc.setQueryData(["adhoc-open", d.id], d);
      setOpenId(d.id);
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/adhoc-dashboards/${id}`),
    onSuccess: (_d, id) => {
      if (openId === id) setOpenId(null);
      qc.invalidateQueries({ queryKey: ["adhoc"] });
    },
  });

  return (
    <div>
      {canEdit(user) && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input
              type="text"
              placeholder={llm ? 'Optional focus, e.g. "why are totals drifting this week?"' : "Optional focus label"}
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              style={{ marginTop: 0, flex: 1, minWidth: 240 }}
            />
            <button
              className="primary"
              onClick={() => generate.mutate()}
              disabled={generate.isPending || !hasProfile}
              title={!hasProfile ? "Profile the dataset first" : undefined}
            >
              {generate.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="bolt" size={14} />}
              {generate.isPending ? "Designing dashboard…" : llm ? "Generate dashboard (AI)" : "Generate dashboard"}
            </button>
          </div>
          <ErrorBox error={generate.error} />
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 16, alignItems: "start" }}>
        <div className="card card-pad">
          <h3>Dashboards</h3>
          {metas.isLoading ? (
            <Spinner />
          ) : !metas.data?.length ? (
            <div className="empty" style={{ padding: 14 }}>None yet — generate one.</div>
          ) : (
            metas.data.map((m) => (
              <div
                key={m.id}
                className="clickable"
                onClick={() => setOpenId(m.id)}
                style={{
                  padding: "8px 10px",
                  borderRadius: 6,
                  cursor: "pointer",
                  background: openId === m.id ? "var(--brand-light)" : undefined,
                  marginBottom: 4,
                }}
              >
                <div style={{ fontWeight: 700, fontSize: 12.5, color: "var(--text-dark)" }}>{m.title}</div>
                <div style={{ fontSize: 11, color: "var(--text-light)" }}>
                  <span className={`badge ${m.origin === "llm" ? "ai" : ""}`} style={{ fontSize: 9.5 }}>
                    {m.origin === "llm" ? "AI" : "auto"}
                  </span>{" "}
                  {m.panel_count} panels · {fmtDateTime(m.created_at)}
                </div>
              </div>
            ))
          )}
        </div>

        <div>
          {!openId ? (
            <div className="card">
              <EmptyState
                title="Pick or generate a dashboard"
                hint="Panels are saved SQL + a chart hint; they re-run against the live source every time you open them."
              />
            </div>
          ) : dashboard.isLoading ? (
            <Spinner label="Running panels against the source…" />
          ) : dashboard.data ? (
            <>
              <div className="toolbar">
                <h3 style={{ fontSize: 15 }}>{dashboard.data.title}</h3>
                {dashboard.data.focus && <span className="badge">focus: {dashboard.data.focus}</span>}
                <div className="right">
                  <button className="small" onClick={() => dashboard.refetch()}>
                    <Icon name="refresh" size={12} /> Refresh
                  </button>
                  {canEdit(user) && (
                    <button className="small danger" onClick={() => remove.mutate(openId)}>
                      Delete
                    </button>
                  )}
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14 }}>
                {dashboard.data.panels.map((p, i) => (
                  <PanelCard key={i} panel={p} />
                ))}
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
