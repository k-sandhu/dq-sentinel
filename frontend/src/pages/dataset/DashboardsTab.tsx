import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type HTMLAttributes, useState } from "react";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { AdhocDashboard, AdhocDashboardMeta, Health, Panel } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import { useConfirm } from "../../components/confirm";
import ErrorBoundary from "../../components/ErrorBoundary";
import PanelChart from "../../components/PanelChart";
import { activateOnKey, EmptyState, ErrorBox, Icon, Spinner } from "../../components/ui";
import { fmtDateTime } from "../../lib/format";

function PanelCard({ panel }: { panel: Panel }) {
  const [showSql, setShowSql] = useState(false);
  const isNumber = panel.viz.type === "number";
  return (
    <div className="card card-pad" style={isNumber ? { gridColumn: "span 1" } : { gridColumn: "span 2" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <h3 style={{ marginBottom: 2 }}>{panel.title}</h3>
        <button
          className="ghost small"
          aria-label={showSql ? "Hide SQL" : "Show SQL"}
          aria-expanded={showSql}
          title="Show SQL"
          onClick={() => setShowSql(!showSql)}
        >
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
        <ErrorBoundary fallback={<div className="error-box">Could not render this chart.</div>}>
          <PanelChart columns={panel.columns} rows={panel.rows} viz={panel.viz} height={isNumber ? 60 : 210} />
        </ErrorBoundary>
      )}
      <div style={{ fontSize: 10.5, color: "var(--text-light)", marginTop: 4 }}>{panel.elapsed_ms} ms</div>
    </div>
  );
}

export default function DashboardsTab({ datasetId, hasProfile }: { datasetId: number; hasProfile: boolean }) {
  const { user } = useAuth();
  // Opening a board RE-RUNS its saved SQL against the source, so the backend gates
  // GET /adhoc-dashboards/{id} on editor (same gate as POST /query/run). Rows must
  // not look activatable to someone who would only ever get a 403 back.
  const canOpen = canEdit(user);
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [focus, setFocus] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);

  const { data: health } = useQuery({ queryKey: qk.health.get(), queryFn: () => api.get<Health>("/health") });
  const llm = health?.llm_enabled ?? false;

  const metas = useQuery({
    queryKey: qk.adhoc.byDataset(datasetId),
    queryFn: () => api.get<AdhocDashboardMeta[]>(`/adhoc-dashboards?dataset_id=${datasetId}`),
  });
  const boards = metas.data;

  // The tab used to open with an empty right pane even when a board existed: the
  // "Pick or generate a dashboard" prompt reads as "there is nothing saved here",
  // which is exactly what pushed analysts into generating a second copy (#259).
  // Default to the most recent board (the list comes back id-desc) and let a
  // click override it — derived rather than an effect, so there is no empty first
  // paint and a board that disappears (deleted here or elsewhere) falls back to
  // the next one instead of leaving a selection pointed at a 404. Opening re-runs
  // the board's SQL against the source, so this stays behind the same editor gate
  // as the rows: a viewer auto-selects nothing.
  const selectedId = !canOpen
    ? null
    : openId !== null && (!boards || boards.some((m) => m.id === openId))
      ? openId
      : (boards?.[0]?.id ?? null);

  const dashboard = useQuery({
    queryKey: qk.adhocOpen.detail(selectedId),
    queryFn: () => api.get<AdhocDashboard>(`/adhoc-dashboards/${selectedId}`),
    enabled: selectedId !== null,
  });

  const generate = useMutation({
    mutationFn: () => api.post<AdhocDashboard>("/adhoc-dashboards/generate", { dataset_id: datasetId, focus }),
    onSuccess: (d) => {
      setFocus("");
      // Seed both caches before the refetch lands: without the list entry the
      // derived selection below would bounce off the new id and show the older
      // board for a beat.
      qc.setQueryData<AdhocDashboardMeta[]>(qk.adhoc.byDataset(datasetId), (prev) =>
        prev ? [d, ...prev.filter((m) => m.id !== d.id)] : prev,
      );
      qc.setQueryData(qk.adhocOpen.detail(d.id), d);
      qc.invalidateQueries({ queryKey: qk.adhoc.all });
      setOpenId(d.id);
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/adhoc-dashboards/${id}`),
    onSuccess: (_d, id) => {
      // Drop it locally too — the derived selection must not land back on the
      // deleted board while the list refetches.
      qc.setQueryData<AdhocDashboardMeta[]>(qk.adhoc.byDataset(datasetId), (prev) =>
        prev?.filter((m) => m.id !== id),
      );
      qc.removeQueries({ queryKey: qk.adhocOpen.detail(id) });
      if (openId === id) setOpenId(null);
      qc.invalidateQueries({ queryKey: qk.adhoc.all });
    },
  });

  // Generation always creates a NEW board — the server derives the title from the
  // dataset (plus the focus), so two runs minutes apart produced two identical
  // `trips overview`s in the QA pass (#259). Same dataset + same focus means the
  // same board, so ask first and point at Refresh, which re-runs the existing
  // panels against the source. The button is disabled while a generate is in
  // flight; this guard covers the second *deliberate* click.
  const trimmedFocus = focus.trim().toLowerCase();
  const duplicateOf = boards?.find((m) => m.focus.trim().toLowerCase() === trimmedFocus) ?? null;

  const startGenerate = async () => {
    if (generate.isPending) return;
    if (duplicateOf) {
      const proceed = await confirm({
        title: "Generate a second dashboard?",
        confirmLabel: "Generate another",
        cancelLabel: "Open the existing one",
        body: (
          <>
            This dataset already has <strong>{duplicateOf.title}</strong> ({duplicateOf.panel_count} panels,
            saved {fmtDateTime(duplicateOf.created_at)}){trimmedFocus ? " for the same focus" : ""}. Generating
            adds a separate board rather than updating it — <strong>Refresh</strong> re-runs the existing
            panels against the source.
          </>
        ),
      });
      if (!proceed) {
        setOpenId(duplicateOf.id);
        return;
      }
    }
    generate.mutate();
  };

  return (
    <div>
      {canOpen && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input
              type="text"
              aria-label="Dashboard focus (optional)"
              placeholder={llm ? 'Optional focus, e.g. "why are totals drifting this week?"' : "Optional focus label"}
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              style={{ marginTop: 0, flex: 1, minWidth: 240 }}
            />
            <button
              className="primary"
              onClick={() => void startGenerate()}
              disabled={generate.isPending || !hasProfile}
              aria-busy={generate.isPending}
              title={
                !hasProfile
                  ? "Profile the dataset first"
                  : duplicateOf
                    ? `This dataset already has “${duplicateOf.title}” — generating adds a second board`
                    : undefined
              }
            >
              {generate.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="bolt" size={14} />}
              {generate.isPending
                ? "Designing dashboard…"
                : duplicateOf
                  ? "Generate another dashboard"
                  : llm
                    ? "Generate dashboard (AI)"
                    : "Generate dashboard"}
            </button>
          </div>
          <ErrorBox error={generate.error} />
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 16, alignItems: "start" }}>
        <div className="card card-pad">
          <h3>Dashboards</h3>
          {!canOpen && (
            <div className="muted" style={{ fontSize: 11.5, marginBottom: 8 }}>
              Opening a dashboard re-runs its saved SQL against the source, which needs editor access
              on this connection.
            </div>
          )}
          <ErrorBox error={metas.error} />
          {metas.isLoading ? (
            <Spinner />
          ) : !boards?.length ? (
            <div className="empty" style={{ padding: 14 }}>
              {canOpen ? "None yet — generate one." : "None yet."}
            </div>
          ) : (
            boards.map((m) => {
              // Only an editor gets an activatable row: no role/tabIndex/handlers for a
              // viewer, so the list reads as reference rather than a wall of 403s.
              const open = () => setOpenId(m.id);
              const interactive: HTMLAttributes<HTMLDivElement> = canOpen
                ? {
                    role: "button",
                    tabIndex: 0,
                    "aria-pressed": selectedId === m.id,
                    onClick: open,
                    onKeyDown: activateOnKey(open),
                  }
                : {};
              return (
                <div
                  key={m.id}
                  className={canOpen ? "clickable" : undefined}
                  {...interactive}
                  style={{
                    padding: "8px 10px",
                    borderRadius: 6,
                    cursor: canOpen ? "pointer" : "default",
                    background: selectedId === m.id ? "var(--brand-light)" : undefined,
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
              );
            })
          )}
        </div>

        <div>
          {generate.isPending ? (
            // Progress belongs where the board will appear: the button alone left
            // the pane looking idle, so a waiting analyst clicked Generate again (#259).
            <div className="card card-pad" aria-live="polite">
              <Spinner label="Designing dashboard — writing panel SQL and running it against the source…" />
            </div>
          ) : selectedId === null ? (
            <div className="card">
              <EmptyState
                title={canOpen ? "Pick or generate a dashboard" : "Dashboards are listed, not runnable here"}
                hint={
                  canOpen
                    ? "Panels are saved SQL + a chart hint; they re-run against the live source every time you open them."
                    : "Panels are saved SQL + a chart hint, and they re-run against the live source on open — so viewing one needs editor access on this dataset's connection."
                }
              />
            </div>
          ) : dashboard.isLoading ? (
            <Spinner label="Running panels against the source…" />
          ) : dashboard.error ? (
            // surface open/refresh failures — a silent blank pane looks like a crash
            <div className="card card-pad">
              <ErrorBox error={dashboard.error} />
              <button className="small" onClick={() => dashboard.refetch()}>
                <Icon name="refresh" size={12} /> Retry
              </button>
            </div>
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
                    <button
                      className="small danger"
                      onClick={async () => {
                        // `selectedId`, not `openId`: the shown board may be the
                        // auto-selected default the analyst never clicked.
                        if (selectedId === null) return;
                        if (
                          await confirm({
                            title: "Delete dashboard",
                            danger: true,
                            confirmLabel: "Delete",
                            body: (
                              <>
                                Delete <strong>{dashboard.data?.title}</strong>? Its saved panels will be
                                removed.
                              </>
                            ),
                          })
                        )
                          remove.mutate(selectedId);
                      }}
                    >
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
