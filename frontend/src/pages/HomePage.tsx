import { useQuery } from "@tanstack/react-query";
import { type CSSProperties, type ReactNode, useState } from "react";
import { Link } from "react-router";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type {
  Dashboard,
  DashboardConsole,
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

const QUALITY_DIMENSIONS = [
  "Completeness",
  "Validity",
  "Freshness",
  "Uniqueness",
  "Consistency",
  "Accuracy",
] as const;

type Tone = "ok" | "warn" | "danger" | "neutral";

function fmtScore(score: number | null | undefined): string {
  if (score == null) return "—";
  return Number.isInteger(score) ? score.toFixed(0) : score.toFixed(1);
}

function sloLabel(status: ScorecardSloStatus | null | undefined): string {
  if (!status) return "unknown";
  return status === "at_risk" ? "at risk" : status;
}

function sloTone(status: ScorecardSloStatus | null | undefined): Tone {
  if (status === "met") return "ok";
  if (status === "at_risk") return "warn";
  if (status === "breached") return "danger";
  return "neutral";
}

function panelError(error: unknown, label: string) {
  return error ? (
    <ErrorBox error={error instanceof Error ? new Error(`${label}: ${error.message}`) : error} />
  ) : null;
}

// ── Tier 1: status ────────────────────────────────────────────────────────────────

function HealthRing({ pct, tone }: { pct: number | null; tone: Tone }) {
  const cls = tone === "danger" ? "ring danger" : tone === "warn" ? "ring warn" : "ring";
  return (
    <div
      className={cls}
      style={{ "--p": pct == null ? 0 : Math.round(pct) } as CSSProperties}
      role="img"
      aria-label={pct == null ? "Checks passing: no data" : `Checks passing: ${pct.toFixed(0)}%`}
    >
      <strong>{pct == null ? "—" : `${pct.toFixed(0)}%`}</strong>
    </div>
  );
}

function Kpi({
  label,
  value,
  tone,
  foot,
  to,
}: {
  label: string;
  value: string;
  tone?: Tone;
  foot?: ReactNode;
  to?: string;
}) {
  const body = (
    <>
      <div className="label">{label}</div>
      <div className={`value${tone && tone !== "neutral" ? ` ${tone}` : ""}`}>{value}</div>
      {foot && <div className="foot">{foot}</div>}
    </>
  );
  return to ? (
    <Link to={to} className="card kpi link">
      {body}
    </Link>
  ) : (
    <div className="card kpi">{body}</div>
  );
}

function StatusTier({
  summary,
  dashboard,
  openIncidents,
}: {
  summary: ScorecardSummary | undefined;
  dashboard: Dashboard | undefined;
  openIncidents: number | null;
}) {
  const passingPct =
    summary && summary.active_checks > 0
      ? (summary.passing_checks / summary.active_checks) * 100
      : dashboard?.pass_rate_7d != null
        ? dashboard.pass_rate_7d * 100
        : null;
  const ringTone: Tone =
    passingPct == null ? "neutral" : passingPct >= 95 ? "ok" : passingPct >= 85 ? "warn" : "danger";
  const coverage =
    summary && summary.total_datasets > 0
      ? (summary.scored_datasets / summary.total_datasets) * 100
      : null;
  const openExc = summary?.open_exceptions ?? dashboard?.open_exceptions;

  return (
    <div className="ov-status">
      <div className="card card-pad ov-ring-card">
        <HealthRing pct={passingPct} tone={ringTone} />
        <div>
          <div className="label" style={{ fontSize: 11, fontWeight: 700, color: "var(--text-light)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
            Checks passing
          </div>
          <div style={{ marginTop: 6, fontSize: 13, color: "var(--text)" }}>
            Quality score <strong>{fmtScore(summary?.score)}</strong>{" "}
            <span className={`pill tone-${sloTone(summary?.slo_status)}`}>{sloLabel(summary?.slo_status)}</span>
          </div>
        </div>
      </div>
      <Kpi
        label="Open incidents"
        value={openIncidents == null ? "—" : fmtNum(openIncidents)}
        tone={openIncidents ? "danger" : "ok"}
        to="/incidents"
      />
      <Kpi
        label="Open exceptions"
        value={fmtNum(openExc)}
        tone={openExc ? "danger" : "ok"}
        to="/exceptions"
      />
      <Kpi
        label="MTTD / MTTR"
        value="—"
        foot={<Link to="/reliability">per-SLA · Reliability →</Link>}
      />
      <Kpi
        label="Coverage"
        value={coverage == null ? "—" : fmtPct(coverage / 100)}
        foot={
          coverage == null
            ? "pending (#116)"
            : `${fmtNum(summary?.scored_datasets)} / ${fmtNum(summary?.total_datasets)} datasets scored`
        }
        to="/datasets"
      />
    </div>
  );
}

// ── Tier 2: trend + attention ───────────────────────────────────────────────────

/** Check-results trend (14 days) from the grant-scoped /dashboard `trend` — replaces
 *  the un-scoped /scorecards/history line so the Overview never aggregates ungranted
 *  datasets (#183 review). */
function RunTrendPanel({
  dashboard,
  isLoading,
  error,
}: {
  dashboard: Dashboard | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  const trend = (dashboard?.trend ?? []).map((t) => ({ ...t, label: t.day.slice(5) }));
  return (
    <div className="card card-pad">
      <div className="section-title compact">
        <h2>Check results</h2>
        <span className="badge">14 days</span>
      </div>
      {error ? (
        panelError(error, "Run results")
      ) : isLoading ? (
        <Spinner label="Loading run results…" />
      ) : trend.length === 0 ? (
        <EmptyState title="No recent runs" hint="Scheduled and manual check runs will appear here." />
      ) : (
        <ResponsiveContainer width="100%" height={210}>
          <BarChart data={trend} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
            <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--border)" }} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} allowDecimals={false} />
            <Tooltip cursor={{ fill: "var(--hover)" }} contentStyle={TOOLTIP_STYLE} />
            <Bar dataKey="passed" stackId="a" fill="var(--ok)" />
            <Bar dataKey="warned" stackId="a" fill="var(--yellow)" />
            <Bar dataKey="failed" stackId="a" fill="var(--danger)" />
            <Bar dataKey="errored" stackId="a" fill="var(--danger-deep)" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

/** 90-day incident heatmap (calendar) from the grant-scoped, server-side
 *  `incident_activity` (UTC date -> count) on /dashboard/console. */
function IncidentHeatmap({
  activity,
  loading,
  error,
}: {
  activity: Record<string, number> | undefined;
  loading: boolean;
  error: unknown;
}) {
  const days = 91; // 13 weeks
  const now = Date.now();
  const cells = Array.from({ length: days }, (_, i) => {
    const d = new Date(now - (days - 1 - i) * 86_400_000);
    const key = d.toISOString().slice(0, 10);
    const n = activity?.[key] ?? 0;
    const q = n === 0 ? 0 : n === 1 ? 1 : n === 2 ? 2 : n <= 4 ? 3 : 4;
    return { key, n, q };
  });
  const total = cells.reduce((s, c) => s + c.n, 0);
  const peak = cells.reduce((m, c) => (c.n > m.n ? c : m), cells[0]);
  return (
    <div className="card card-pad">
      <div className="section-title compact">
        <h2>Incident activity</h2>
        <span className="badge">90 days (UTC)</span>
      </div>
      {error ? (
        panelError(error, "Incident activity")
      ) : loading ? (
        <Spinner label="Loading incident activity…" />
      ) : (
        <div
          className="cal"
          role="img"
          aria-label={
            total === 0
              ? "No incidents opened in the last 90 days"
              : `${total} incident${total === 1 ? "" : "s"} opened in the last 90 days; busiest day ${peak.key} with ${peak.n}`
          }
        >
          {cells.map((c) => (
            <i
              key={c.key}
              className={c.q ? `q${c.q}` : ""}
              role="img"
              aria-label={`${c.key}: ${c.n} incident${c.n === 1 ? "" : "s"}`}
              title={`${c.key}: ${c.n} incident${c.n === 1 ? "" : "s"}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** Quality-by-dimension scorecard. No per-quality-dimension backend score exists yet
 *  (rollups are domain/team) — render an explicit empty state, tracked on #116. */
function DimensionScorecard() {
  return (
    <div className="card card-pad">
      <div className="section-title compact">
        <h2>Quality by dimension</h2>
        <span className="badge">pending #116</span>
      </div>
      <div className="dim-grid" aria-hidden="true">
        {QUALITY_DIMENSIONS.map((d) => (
          <div key={d} className="card dim-tile">
            <div className="dn">{d}</div>
            <div className="dv">—</div>
            <div className="meter">
              <span style={{ width: "0%" }} />
            </div>
          </div>
        ))}
      </div>
      <div className="sub" style={{ marginTop: 10 }}>
        Per-dimension quality scores aren&rsquo;t exposed by the scorecard API yet — tracked on #116.
      </div>
    </div>
  );
}

function NeedsAttention({ console: c, summary }: { console: DashboardConsole | undefined; summary: ScorecardSummary | undefined }) {
  const items: { key: string; title: string; meta: string; to: string }[] = [];
  for (const chk of (c?.failing_now ?? []).slice(0, 4)) {
    items.push({
      key: `chk-${chk.id}`,
      title: chk.name,
      meta: `failing · ${chk.severity}`,
      to: `/datasets/${chk.dataset_id}/exceptions`,
    });
  }
  for (const ds of (summary?.top_failing_datasets ?? []).slice(0, 3)) {
    items.push({
      key: `ds-${ds.dataset_id}`,
      title: ds.display_name || `${ds.schema_name ? `${ds.schema_name}.` : ""}${ds.table_name}`,
      meta: `${fmtNum(ds.open_exceptions)} open exceptions`,
      to: `/datasets/${ds.dataset_id}/exceptions`,
    });
  }
  return (
    <div className="card card-pad">
      <div className="section-title compact">
        <h2>Needs attention</h2>
        <Link to="/my-work" className="btn small">My work</Link>
      </div>
      {items.length === 0 ? (
        <EmptyState title="Nothing needs attention" hint="No failing checks or high-exception datasets right now." />
      ) : (
        <div className="needs-rail">
          {items.map((it) => (
            <Link key={it.key} to={it.to} className="dense-item clickable">
              <div className="row-title-link">{it.title}</div>
              <div className="sub">{it.meta}</div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Tier 3: datasets-by-risk worklist ─────────────────────────────────────────────

type RiskFilter = "all" | "failing" | "exceptions";

function DatasetsByRisk({
  summary,
  dashboard,
}: {
  summary: ScorecardSummary | undefined;
  dashboard: Dashboard | undefined;
}) {
  const [filter, setFilter] = useState<RiskFilter>("all");
  const rows = summary
    ? summary.top_failing_datasets.map((d) => ({
        id: d.dataset_id,
        name: d.display_name || `${d.schema_name ? `${d.schema_name}.` : ""}${d.table_name}`,
        score: d.score,
        status: d.slo_status,
        exceptions: d.open_exceptions,
        owner: [d.domain, d.team, d.owner].filter(Boolean).join(" / "),
      }))
    : (dashboard?.worst_datasets ?? []).map((d) => ({
        id: d.id,
        name: `${d.schema_name ? `${d.schema_name}.` : ""}${d.table_name}`,
        score: null as number | null,
        status: null as ScorecardSloStatus | null,
        exceptions: d.open_exceptions,
        owner: d.connection_name,
      }));
  const shown = rows.filter((r) =>
    filter === "all"
      ? true
      : filter === "failing"
        ? r.status === "breached" || r.status === "at_risk"
        : (r.exceptions ?? 0) > 0,
  );
  const CHIPS: { id: RiskFilter; label: string }[] = [
    { id: "all", label: "All" },
    { id: "failing", label: "Failing SLO" },
    { id: "exceptions", label: "With exceptions" },
  ];

  return (
    <div className="card">
      <div className="card-pad section-title" style={{ marginBottom: 0 }}>
        <h2 style={{ fontSize: 14 }}>Datasets by risk</h2>
        <div className="seg" role="tablist" aria-label="Filter datasets by risk">
          {CHIPS.map((chip) => (
            <button
              key={chip.id}
              type="button"
              role="tab"
              aria-selected={filter === chip.id}
              className={filter === chip.id ? "on" : ""}
              onClick={() => setFilter(chip.id)}
            >
              {chip.label}
            </button>
          ))}
        </div>
      </div>
      {shown.length === 0 ? (
        <EmptyState title="No datasets match" hint="Nothing in this risk bucket right now." />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Score</th>
                <th>SLO</th>
                <th className="num">Open exceptions</th>
                <th>Owner</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link to={`/datasets/${r.id}`} className="row-title-link">{r.name}</Link>
                  </td>
                  <td>{fmtScore(r.score)}</td>
                  <td>{r.status ? <span className={`pill tone-${sloTone(r.status)}`}>{sloLabel(r.status)}</span> : "—"}</td>
                  <td className="num">
                    {(r.exceptions ?? 0) > 0 ? (
                      <Link to={`/datasets/${r.id}/exceptions`} className="pill tone-danger">{fmtNum(r.exceptions)}</Link>
                    ) : (
                      fmtNum(r.exceptions)
                    )}
                  </td>
                  <td>{r.owner || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── page ──────────────────────────────────────────────────────────────────────────

export default function HomePage() {
  const dashboardQuery = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/dashboard"), refetchInterval: 30_000 });
  const summaryQuery = useQuery({ queryKey: ["scorecards", "summary"], queryFn: () => api.get<ScorecardSummary>("/scorecards/summary"), refetchInterval: 60_000, retry: false });
  const consoleQuery = useQuery({ queryKey: ["dashboard", "console"], queryFn: () => api.get<DashboardConsole>("/dashboard/console"), refetchInterval: 60_000, retry: false });

  const dashboard = dashboardQuery.data;
  const summary = summaryQuery.data;
  const consoleData = consoleQuery.data;
  const openIncidents = consoleData ? consoleData.open_incidents : null;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Overview</h1>
          <div className="sub">
            {dashboard?.llm_enabled ? (
              <span className="badge ai">AI features enabled</span>
            ) : dashboard ? (
              <span className="badge">LLM disabled — heuristic mode</span>
            ) : (
              <span className="badge">Loading…</span>
            )}{" "}
            {dashboard && (
              <Link to="/runs?since=24h" className="badge badge-link">{fmtNum(dashboard.runs_24h)} runs · last 24h</Link>
            )}
          </div>
        </div>
        <div className="header-actions">
          <Link to="/connections" className="btn"><Icon name="plus" size={14} />Add data</Link>
        </div>
      </div>

      {/* Tier 1 — status */}
      <StatusTier summary={summary} dashboard={dashboard} openIncidents={openIncidents} />

      {/* Tier 2 — trend + attention */}
      <div className="split" style={{ margin: "16px 0" }}>
        <RunTrendPanel dashboard={dashboard} isLoading={dashboardQuery.isLoading} error={dashboardQuery.error} />
        <IncidentHeatmap
          activity={consoleData?.incident_activity}
          loading={consoleQuery.isLoading}
          error={consoleQuery.error}
        />
      </div>
      <div className="split" style={{ marginBottom: 16 }}>
        <DimensionScorecard />
        <NeedsAttention console={consoleData} summary={summary} />
      </div>

      {/* Tier 3 — datasets-by-risk worklist + recent runs */}
      <div style={{ marginBottom: 16 }}>
        <DatasetsByRisk summary={summary} dashboard={dashboard} />
      </div>
      <div className="card">
        <div className="card-pad section-title" style={{ marginBottom: 0 }}>
          <h2 style={{ fontSize: 14 }}>Recent runs</h2>
          <Link to="/runs" className="btn small">All runs</Link>
        </div>
        {dashboardQuery.error ? (
          <div className="card-pad">{panelError(dashboardQuery.error, "Recent runs")}</div>
        ) : dashboardQuery.isLoading ? (
          <Spinner label="Loading recent runs…" />
        ) : dashboard ? (
          <RunsTable runs={dashboard.recent_runs} />
        ) : (
          <EmptyState title="No recent runs" hint="Check run history will appear here." />
        )}
      </div>
    </div>
  );
}
