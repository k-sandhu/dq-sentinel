import { useQuery } from "@tanstack/react-query";
import { type CSSProperties } from "react";
import { Link } from "react-router";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type {
  Dashboard,
  Dataset,
  ScorecardHistory,
  ScorecardHistoryPoint,
  ScorecardRollup,
  ScorecardSloStatus,
  ScorecardSummary,
} from "../api/types";
import RunsTable from "../components/RunsTable";
import { EmptyState, ErrorBox, Icon, Spinner } from "../components/ui";
import { fmtNum, fmtPct } from "../lib/format";

const TOOLTIP_STYLE = {
  fontSize: 12,
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--card)",
  color: "var(--text-dark)",
};

const AXIS = { fontSize: 11, fill: "var(--text-light)" };

function clampScore(score: number | null | undefined): number {
  if (score == null || Number.isNaN(score)) return 0;
  return Math.max(0, Math.min(100, score));
}

function fmtScore(score: number | null | undefined): string {
  if (score == null) return "-";
  return Number.isInteger(score) ? score.toFixed(0) : score.toFixed(1);
}

function fmtPoints(points: number | null | undefined): string {
  if (points == null) return "-";
  return `${Math.abs(points).toFixed(1)} pts`;
}

function fmtDelta(delta: number | null | undefined): string {
  if (delta == null) return "-";
  if (delta === 0) return "0.0 pts";
  return `${delta > 0 ? "+" : ""}${delta.toFixed(1)} pts`;
}

function sloLabel(status: ScorecardSloStatus | null | undefined): string {
  if (!status) return "unknown";
  return status === "at_risk" ? "at risk" : status;
}

function sloTone(status: ScorecardSloStatus | null | undefined): "ok" | "warn" | "danger" | "neutral" {
  if (status === "met") return "ok";
  if (status === "at_risk") return "warn";
  if (status === "breached") return "danger";
  return "neutral";
}

function scoreTone(status: ScorecardSloStatus | null | undefined, score: number | null | undefined): string {
  if (status === "unknown" || status === "disabled" || score == null) return "unknown";
  if (status === "breached") return "breached";
  if (status === "at_risk") return "at-risk";
  if (score != null && score < 80) return "at-risk";
  return "met";
}

function scoreStyle(score: number | null | undefined): CSSProperties {
  return { "--score": clampScore(score) } as CSSProperties;
}

function trendDelta(history: ScorecardHistoryPoint[]) {
  const scored = history.filter((p) => p.score != null);
  if (scored.length >= 2) {
    return {
      label: "history delta",
      value: (scored[scored.length - 1].score ?? 0) - (scored[0].score ?? 0),
    };
  }
  return { label: "trend delta", value: null };
}

function datasetDriverLink(datasetId: number | null | undefined): string | null {
  return datasetId != null ? `/datasets/${datasetId}/exceptions` : null;
}

function SloPill({ status }: { status: ScorecardSloStatus | null | undefined }) {
  return <span className={`pill tone-${sloTone(status)}`}>{sloLabel(status)}</span>;
}

function ScoreRing({
  score,
  status,
}: {
  score: number | null | undefined;
  status: ScorecardSloStatus | null | undefined;
}) {
  return (
    <div className={`score-ring scorecard-ring ${scoreTone(status, score)}`} style={scoreStyle(score)}>
      <strong>{fmtScore(score)}</strong>
    </div>
  );
}

function ScoreMetric({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "ok" | "warn" | "danger";
}) {
  return (
    <div className={`scorecard-metric ${tone ?? ""}`}>
      <div className="scorecard-metric-label">{label}</div>
      <div className="scorecard-metric-value">{value}</div>
      {hint && <div className="scorecard-metric-hint">{hint}</div>}
    </div>
  );
}

function ScoreCell({ score }: { score: number | null | undefined }) {
  const clamped = clampScore(score);
  return (
    <div className="scorecell">
      <span>{fmtScore(score)}</span>
      <div className="scorecell-bar" aria-hidden="true">
        <div style={{ width: `${clamped}%` }} />
      </div>
    </div>
  );
}

function panelError(error: unknown, label: string) {
  return error ? <ErrorBox error={error instanceof Error ? new Error(`${label}: ${error.message}`) : error} /> : null;
}

function ScorecardTopBand({
  summary,
  dashboard,
  history,
  loading,
  error,
}: {
  summary: ScorecardSummary | undefined;
  dashboard: Dashboard | undefined;
  history: ScorecardHistoryPoint[];
  loading: boolean;
  error: unknown;
}) {
  const delta = trendDelta(history);
  const activeChecks = summary?.active_checks ?? dashboard?.active_checks;
  const openExceptions = summary?.open_exceptions ?? dashboard?.open_exceptions;
  const datasetCount = summary?.total_datasets ?? dashboard?.datasets;

  return (
    <div className="scorecard-top">
      <div className="card card-pad scorecard-summary-card">
        <div className="scorecard-summary-main">
          <ScoreRing score={summary?.score} status={summary?.slo_status} />
          <div className="scorecard-summary-copy">
            <div className="scorecard-eyebrow">Global quality score</div>
            <div className="scorecard-score-line">
              <span>{fmtScore(summary?.score)}</span>
              <SloPill status={summary?.slo_status ?? "unknown"} />
            </div>
            <div className="scorecard-summary-meta">
              Target {fmtScore(summary?.slo_target)}
              {summary?.score_gap != null ? `, gap ${fmtPoints(summary.score_gap)}` : ""}
            </div>
          </div>
        </div>
        {loading && !summary && <div className="scorecard-soft-note">Loading scorecard summary...</div>}
        {error && !summary ? panelError(error, "Scorecard summary") : null}
      </div>

      <div className="scorecard-metric-grid">
        <ScoreMetric label="SLO met" value={fmtNum(summary?.slo_met)} tone="ok" hint={`${fmtNum(datasetCount)} datasets`} />
        <ScoreMetric label="At risk" value={fmtNum(summary?.slo_at_risk)} tone="warn" />
        <ScoreMetric label="Breached" value={fmtNum(summary?.slo_breached)} tone="danger" />
        <ScoreMetric
          label={delta.label}
          value={fmtDelta(delta.value)}
          tone={delta.value == null ? undefined : delta.value < 0 ? "danger" : "ok"}
        />
        <ScoreMetric label="Active checks" value={fmtNum(activeChecks)} />
        <ScoreMetric
          label="Open exceptions"
          value={fmtNum(openExceptions)}
          tone={openExceptions ? "danger" : "ok"}
        />
      </div>
    </div>
  );
}

function ScoreTrendPanel({
  points,
  isLoading,
  error,
}: {
  points: ScorecardHistoryPoint[];
  isLoading: boolean;
  error: unknown;
}) {
  const trend = points.map((p) => ({
    ...p,
    day: p.snapshot_date.slice(5),
  }));
  const hasSnapshots = trend.some((p) => p.score != null);
  const hasTarget = trend.some((p) => p.slo_target != null);

  return (
    <div className="card card-pad scorecard-panel">
      <div className="section-title compact">
        <h2>Quality score trend</h2>
        <span className="badge">90 days</span>
      </div>
      {error ? (
        panelError(error, "Scorecard history")
      ) : isLoading ? (
        <Spinner label="Loading score history..." />
      ) : !hasSnapshots ? (
        <EmptyState title="No score snapshots yet" hint="Current scorecards still render while the worker starts collecting daily history." />
      ) : (
        <>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={trend} margin={{ top: 8, right: 10, left: -8, bottom: 0 }}>
              <CartesianGrid stroke="var(--border-light)" vertical={false} />
              <XAxis dataKey="day" tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--border)" }} />
              <YAxis
                domain={[0, 100]}
                tick={AXIS}
                tickLine={false}
                axisLine={false}
                width={38}
              />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              {hasTarget && (
                <Line
                  type="monotone"
                  dataKey="slo_target"
                  name="SLO target"
                  stroke="var(--warn-strong)"
                  strokeDasharray="4 4"
                  strokeWidth={1.5}
                  dot={false}
                  connectNulls
                />
              )}
              <Line
                type="monotone"
                dataKey="score"
                name="Quality score"
                stroke="var(--brand)"
                strokeWidth={2.5}
                dot={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
          <div className="legend-row">
            <span><span className="swatch" style={{ background: "var(--brand)" }} />quality score</span>
            {hasTarget && <span><span className="swatch" style={{ background: "var(--warn-strong)" }} />SLO target</span>}
          </div>
        </>
      )}
    </div>
  );
}

type DriverItem = {
  key: string;
  title: string;
  meta: string;
  to: string | null;
  score: number | null;
  loss: number | null;
  exceptions: number | null;
  status: ScorecardSloStatus | null;
};

function buildDriverItems(summary: ScorecardSummary | undefined, dashboard: Dashboard | undefined) {
  if (summary) {
    const datasetDrivers: DriverItem[] = summary.top_failing_datasets.slice(0, 4).map((dataset) => ({
      key: `dataset-${dataset.dataset_id}`,
      title: dataset.display_name || `${dataset.schema_name ? `${dataset.schema_name}.` : ""}${dataset.table_name}`,
      meta: [dataset.domain, dataset.team, dataset.owner].filter(Boolean).join(" / ") || dataset.importance,
      to: datasetDriverLink(dataset.dataset_id),
      score: dataset.score,
      loss: dataset.score_gap,
      exceptions: dataset.open_exceptions,
      status: dataset.slo_status,
    }));

    const rollupDrivers: DriverItem[] = summary.worst_rollups.slice(0, 3).map((rollup) => ({
      key: `rollup-${rollup.dimension}-${rollup.key}`,
      title: rollup.label || "Unassigned",
      meta: `${rollup.dimension} / ${fmtNum(rollup.total_datasets)} datasets`,
      to: null,
      score: rollup.score,
      loss: rollup.score_gap,
      exceptions: rollup.open_exceptions,
      status: rollup.slo_status,
    }));

    return [...datasetDrivers, ...rollupDrivers].slice(0, 6);
  }

  return (dashboard?.worst_datasets ?? []).slice(0, 6).map((dataset: Dataset) => ({
    key: `dataset-${dataset.id}`,
    title: `${dataset.schema_name ? `${dataset.schema_name}.` : ""}${dataset.table_name}`,
    meta: dataset.connection_name,
    to: `/datasets/${dataset.id}/exceptions`,
    score: null as number | null,
    loss: null as number | null,
    exceptions: dataset.open_exceptions,
    status: null as ScorecardSloStatus | null,
  }));
}

function ScoreDriversPanel({
  summary,
  dashboard,
  isLoading,
  error,
}: {
  summary: ScorecardSummary | undefined;
  dashboard: Dashboard | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  const drivers = buildDriverItems(summary, dashboard);

  return (
    <div className="card card-pad scorecard-panel">
      <div className="section-title compact">
        <h2>Score drivers</h2>
        <Link to="/datasets" className="btn small">Datasets</Link>
      </div>
      {error && !summary && !dashboard ? (
        panelError(error, "Score drivers")
      ) : isLoading && !summary && !dashboard ? (
        <Spinner label="Loading drivers..." />
      ) : drivers.length === 0 ? (
        <EmptyState title="No active score drivers" hint="No failing datasets or exception pressure are currently contributing to score loss." />
      ) : (
        <div className="scorecard-driver-list">
          {drivers.map((driver) => {
            const body = (
              <>
                <div className="scorecard-driver-main">
                  <div className="scorecard-driver-title">{driver.title}</div>
                  <div className="scorecard-driver-meta">{driver.meta}</div>
                </div>
                <div className="scorecard-driver-stats">
                  {driver.status && <SloPill status={driver.status} />}
                  {driver.score != null && <span className="badge">score {fmtScore(driver.score)}</span>}
                  {driver.loss != null && <span className="badge">gap {fmtPoints(driver.loss)}</span>}
                  {driver.exceptions != null && (
                    <span className={`badge ${driver.exceptions ? "scorecard-badge-danger" : ""}`}>
                      {fmtNum(driver.exceptions)} exceptions
                    </span>
                  )}
                </div>
              </>
            );
            return driver.to ? (
              <Link key={driver.key} to={driver.to} className="scorecard-driver-row">
                {body}
              </Link>
            ) : (
              <div key={driver.key} className="scorecard-driver-row">
                {body}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function RollupTable({
  title,
  rows,
  isLoading,
  error,
}: {
  title: string;
  rows: ScorecardRollup[];
  isLoading: boolean;
  error: unknown;
}) {
  return (
    <div className="card scorecard-rollup-card">
      <div className="card-pad scorecard-rollup-head">
        <div>
          <h2>{title}</h2>
          <div className="sub">Score, SLO posture, and exception pressure</div>
        </div>
      </div>
      {error ? (
        <div className="card-pad">{panelError(error, title)}</div>
      ) : isLoading ? (
        <div className="card-pad"><Spinner label={`Loading ${title.toLowerCase()}...`} /></div>
      ) : rows.length === 0 ? (
        <EmptyState title="No rollups yet" hint="Rollups appear once datasets have domain or team metadata." />
      ) : (
        <div className="table-wrap">
          <table className="data scorecard-rollup-table">
            <thead>
              <tr>
                <th>{title.replace(" rollups", "")}</th>
                <th>Score</th>
                <th>SLO</th>
                <th className="num">Datasets</th>
                <th className="num">Breached</th>
                <th className="num">Open exceptions</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.dimension}-${row.key}`}>
                  <td>
                    <div className="row-title-link">{row.label || "Unassigned"}</div>
                    <div className="scorecard-row-sub">{row.key || "unassigned"}</div>
                  </td>
                  <td><ScoreCell score={row.score} /></td>
                  <td><SloPill status={row.slo_status} /></td>
                  <td className="num">{fmtNum(row.total_datasets)}</td>
                  <td className="num" style={{ color: row.slo_breached ? "var(--danger-dark)" : undefined, fontWeight: row.slo_breached ? 700 : 400 }}>
                    {fmtNum(row.slo_breached)}
                  </td>
                  <td className="num" style={{ color: row.open_exceptions ? "var(--danger-dark)" : undefined, fontWeight: row.open_exceptions ? 700 : 400 }}>
                    {fmtNum(row.open_exceptions)}
                  </td>
                  <td className="num">
                    <span className="scorecard-row-sub">-</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RunResultsPanel({
  dashboard,
  isLoading,
  error,
}: {
  dashboard: Dashboard | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  const trend = (dashboard?.trend ?? []).map((t) => ({ ...t, day: t.day.slice(5) }));

  return (
    <div className="card card-pad">
      <div className="section-title" style={{ margin: "0 0 12px" }}>
        <h2>Run results - last 14 days</h2>
        <Link to="/runs" className="btn small">
          <Icon name="play" size={12} />
          Runs
        </Link>
      </div>
      {error ? (
        panelError(error, "Operational dashboard")
      ) : isLoading ? (
        <Spinner label="Loading run results..." />
      ) : trend.length === 0 ? (
        <EmptyState title="No recent runs" hint="Scheduled and manual check runs will appear here." />
      ) : (
        <>
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={trend} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
              <XAxis
                dataKey="day"
                tick={AXIS}
                tickLine={false}
                axisLine={{ stroke: "var(--border)" }}
              />
              <YAxis
                tick={AXIS}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
              />
              <Tooltip cursor={{ fill: "var(--hover)" }} contentStyle={TOOLTIP_STYLE} />
              <Bar dataKey="passed" stackId="a" fill="var(--ok)" />
              <Bar dataKey="warned" stackId="a" fill="var(--yellow)" />
              <Bar dataKey="failed" stackId="a" fill="var(--danger)" />
              <Bar dataKey="errored" stackId="a" fill="var(--danger-deep)" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <div className="legend-row">
            <span><span className="swatch" style={{ background: "var(--ok)" }} />passed</span>
            <span><span className="swatch" style={{ background: "var(--yellow)" }} />warned</span>
            <span><span className="swatch" style={{ background: "var(--danger)" }} />failed</span>
            <span><span className="swatch" style={{ background: "var(--danger-deep)" }} />errored</span>
          </div>
        </>
      )}
    </div>
  );
}

function OperationalCountsPanel({
  dashboard,
  isLoading,
  error,
}: {
  dashboard: Dashboard | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  return (
    <div className="card card-pad">
      <div className="section-title" style={{ margin: "0 0 12px" }}>
        <h2>Operational posture</h2>
        <Link to="/checks" className="btn small">Checks</Link>
      </div>
      {error ? (
        panelError(error, "Operational posture")
      ) : isLoading ? (
        <Spinner label="Loading operations..." />
      ) : !dashboard ? (
        <EmptyState title="No operational data" hint="Dashboard counts are unavailable." />
      ) : (
        <div className="scorecard-ops-grid">
          <ScoreMetric label="Datasets monitored" value={fmtNum(dashboard.datasets)} />
          <ScoreMetric label="Failing checks" value={fmtNum(dashboard.failing_checks)} tone={dashboard.failing_checks ? "danger" : "ok"} />
          <ScoreMetric label="Runs in 24h" value={fmtNum(dashboard.runs_24h)} />
          <ScoreMetric label="7-day pass rate" value={fmtPct(dashboard.pass_rate_7d)} />
        </div>
      )}
    </div>
  );
}

export default function HomePage() {
  const dashboardQuery = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get<Dashboard>("/dashboard"),
    refetchInterval: 30_000,
  });

  const summaryQuery = useQuery({
    queryKey: ["scorecards", "summary"],
    queryFn: () => api.get<ScorecardSummary>("/scorecards/summary"),
    refetchInterval: 60_000,
    retry: false,
  });

  const domainRollupsQuery = useQuery({
    queryKey: ["scorecards", "rollups", "domain"],
    queryFn: () => api.get<ScorecardRollup[]>("/scorecards/rollups?dimension=domain"),
    refetchInterval: 60_000,
    retry: false,
  });

  const teamRollupsQuery = useQuery({
    queryKey: ["scorecards", "rollups", "team"],
    queryFn: () => api.get<ScorecardRollup[]>("/scorecards/rollups?dimension=team"),
    refetchInterval: 60_000,
    retry: false,
  });

  const historyQuery = useQuery({
    queryKey: ["scorecards", "history", "global", 90],
    queryFn: () => api.get<ScorecardHistory>("/scorecards/history?grain=global&days=90"),
    refetchInterval: 60_000,
    retry: false,
  });

  const dashboard = dashboardQuery.data;
  const summary = summaryQuery.data;
  const history = historyQuery.data?.points ?? [];

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Executive data quality scorecard</h1>
          <div className="sub">
            {dashboard?.llm_enabled ? (
              <span className="badge ai">AI features enabled</span>
            ) : dashboard ? (
              <span className="badge" title="Set ANTHROPIC_API_KEY to enable LLM check generation, exploration and RCA">
                LLM disabled - heuristic mode
              </span>
            ) : (
              <span className="badge">Loading operations</span>
            )}{" "}
            {dashboard && <span className="badge">{fmtNum(dashboard.runs_24h)} runs in 24h</span>}{" "}
            {dashboard?.pass_rate_7d != null && (
              <span className="badge">7-day pass rate {fmtPct(dashboard.pass_rate_7d)}</span>
            )}
          </div>
        </div>
        <div className="header-actions">
          <Link to="/connections" className="btn">
            <Icon name="plus" size={14} />
            Add data
          </Link>
        </div>
      </div>

      <ScorecardTopBand
        summary={summary}
        dashboard={dashboard}
        history={history}
        loading={summaryQuery.isLoading}
        error={summaryQuery.error}
      />

      <div className="scorecard-dashboard-grid">
        <ScoreTrendPanel points={history} isLoading={historyQuery.isLoading} error={historyQuery.error} />
        <ScoreDriversPanel
          summary={summary}
          dashboard={dashboard}
          isLoading={summaryQuery.isLoading || dashboardQuery.isLoading}
          error={summaryQuery.error ?? dashboardQuery.error}
        />
      </div>

      <div className="scorecard-rollup-grid">
        <RollupTable
          title="Domain rollups"
          rows={domainRollupsQuery.data ?? []}
          isLoading={domainRollupsQuery.isLoading}
          error={domainRollupsQuery.error}
        />
        <RollupTable
          title="Team rollups"
          rows={teamRollupsQuery.data ?? []}
          isLoading={teamRollupsQuery.isLoading}
          error={teamRollupsQuery.error}
        />
      </div>

      <div className="split" style={{ marginBottom: 16 }}>
        <RunResultsPanel dashboard={dashboard} isLoading={dashboardQuery.isLoading} error={dashboardQuery.error} />
        <OperationalCountsPanel dashboard={dashboard} isLoading={dashboardQuery.isLoading} error={dashboardQuery.error} />
      </div>

      <div className="card">
        <div className="card-pad" style={{ paddingBottom: 0 }}>
          <div className="section-title" style={{ margin: 0 }}>
            <h2 style={{ fontSize: 14 }}>Recent runs</h2>
            <Link to="/runs" className="btn small">All runs</Link>
          </div>
        </div>
        {dashboardQuery.error ? (
          <div className="card-pad">{panelError(dashboardQuery.error, "Recent runs")}</div>
        ) : dashboardQuery.isLoading ? (
          <Spinner label="Loading recent runs..." />
        ) : dashboard ? (
          <RunsTable runs={dashboard.recent_runs} />
        ) : (
          <EmptyState title="No recent runs" hint="Check run history will appear here." />
        )}
      </div>
    </div>
  );
}
