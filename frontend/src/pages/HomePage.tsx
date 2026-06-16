import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { Dashboard } from "../api/types";
import RunsTable from "../components/RunsTable";
import { EmptyState, ErrorBox, Icon, Spinner, StatCard, StatusPill } from "../components/ui";
import { fmtNum, fmtPct } from "../lib/format";

type TrendDatum = Dashboard["trend"][number] & { date: string; day: string };

const TOOLTIP_STYLE = {
  fontSize: 12,
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--card)",
  color: "var(--text-dark)",
};

export default function HomePage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get<Dashboard>("/dashboard"),
    refetchInterval: 30_000,
  });

  if (isLoading) return <Spinner label="Loading dashboard…" />;
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return null;

  const trend: TrendDatum[] = data.trend.map((t) => ({ ...t, date: t.day, day: t.day.slice(5) }));
  const runsFor = (params: Record<string, string>) => `/runs?${new URLSearchParams(params).toString()}`;
  const openRunsFor = (date: string, status?: string) => {
    const params: Record<string, string> = { day: date };
    if (status) params.status = status;
    navigate(runsFor(params));
  };
  const trendClick = (status: string) => (entry: unknown) => {
    const point = (entry as { payload?: TrendDatum }).payload;
    if (point) openRunsFor(point.date, status);
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Data quality overview</h1>
          <div className="sub">
            {data.llm_enabled ? (
              <span className="badge ai">AI features enabled</span>
            ) : (
              <span className="badge" title="Set ANTHROPIC_API_KEY to enable LLM check generation, exploration and RCA">
                LLM disabled — heuristic mode
              </span>
            )}{" "}
            <Link to={runsFor({ since: "24h" })} className="badge badge-link">
              {fmtNum(data.runs_24h)} runs in 24h
            </Link>{" "}
            {data.pass_rate_7d != null && (
              <Link to={runsFor({ since: "7d" })} className="badge badge-link">
                7-day pass rate {fmtPct(data.pass_rate_7d)}
              </Link>
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

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <StatCard
          label="Datasets monitored"
          value={fmtNum(data.datasets)}
          to="/datasets"
          title="Open monitored datasets"
          ariaLabel={`${fmtNum(data.datasets)} monitored datasets. Open datasets.`}
        />
        <StatCard
          label="Active checks"
          value={fmtNum(data.active_checks)}
          hint={data.proposed_checks ? `${data.proposed_checks} proposals awaiting review` : undefined}
          to="/checks?status=active"
          title="Open active checks"
          ariaLabel={`${fmtNum(data.active_checks)} active checks. Open active checks.`}
        />
        <StatCard
          label="Failing checks"
          value={fmtNum(data.failing_checks)}
          tone={data.failing_checks ? "danger" : "ok"}
          hint={`${fmtNum(data.runs_24h)} runs in 24h`}
          to="/checks?status=active&last_status=fail&last_status=error"
          title="Open active checks with failing or errored latest results"
          ariaLabel={`${fmtNum(data.failing_checks)} failing checks. Open failing checks.`}
        />
        <StatCard
          label="Open exceptions"
          value={fmtNum(data.open_exceptions)}
          tone={data.open_exceptions ? "danger" : "ok"}
          hint={data.pass_rate_7d != null ? `7-day pass rate ${fmtPct(data.pass_rate_7d)}` : undefined}
          to="/exceptions?status=open"
          title="Open the triage queue filtered to open exceptions"
          ariaLabel={`${fmtNum(data.open_exceptions)} open exceptions. Open triage queue.`}
        />
      </div>

      <div className="split" style={{ marginBottom: 16 }}>
        <div className="card card-pad">
          <div className="section-title" style={{ margin: "0 0 12px" }}>
            <h2>Run results — last 14 days</h2>
            <Link to="/runs" className="btn small">
              <Icon name="play" size={12} />
              Runs
            </Link>
          </div>
          <ResponsiveContainer width="100%" height={210} className="drilldown-chart">
            <BarChart data={trend} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
              <XAxis
                dataKey="day"
                tick={{ fontSize: 11, fill: "var(--text-light)" }}
                tickLine={false}
                axisLine={{ stroke: "var(--border)" }}
              />
              <YAxis
                tick={{ fontSize: 11, fill: "var(--text-light)" }}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
              />
              <Tooltip cursor={{ fill: "var(--hover)" }} contentStyle={TOOLTIP_STYLE} />
              <Bar dataKey="passed" stackId="a" fill="var(--ok)" onClick={trendClick("pass")} cursor="pointer" />
              <Bar dataKey="warned" stackId="a" fill="var(--yellow)" onClick={trendClick("warn")} cursor="pointer" />
              <Bar dataKey="failed" stackId="a" fill="var(--danger)" onClick={trendClick("fail")} cursor="pointer" />
              <Bar
                dataKey="errored"
                stackId="a"
                fill="var(--danger-deep)"
                radius={[2, 2, 0, 0]}
                onClick={trendClick("error")}
                cursor="pointer"
              />
            </BarChart>
          </ResponsiveContainer>
          <div className="legend-row">
            <span><span className="swatch" style={{ background: "var(--ok)" }} />passed</span>
            <span><span className="swatch" style={{ background: "var(--yellow)" }} />warned</span>
            <span><span className="swatch" style={{ background: "var(--danger)" }} />failed</span>
            <span><span className="swatch" style={{ background: "var(--danger-deep)" }} />errored</span>
          </div>
        </div>

        <div className="card card-pad">
          <div className="section-title" style={{ margin: "0 0 12px" }}>
            <h2>Datasets needing attention</h2>
            <Link to="/datasets" className="btn small">All datasets</Link>
          </div>
          {data.worst_datasets.length === 0 ? (
            <EmptyState title="No open exceptions" hint="Every monitored dataset is currently clean." />
          ) : (
            <div className="dense-list">
              {data.worst_datasets.map((d) => (
                <div
                  key={d.id}
                  className="dense-item clickable"
                  role="link"
                  tabIndex={0}
                  onClick={() => navigate(`/datasets/${d.id}/exceptions`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      navigate(`/datasets/${d.id}/exceptions`);
                    }
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                    <div>
                      <div className="title">
                        {d.schema_name ? `${d.schema_name}.` : ""}
                        {d.table_name}
                      </div>
                      <div className="meta">{d.connection_name}</div>
                    </div>
                    <StatusPill value={d.health} />
                  </div>
                  <div className="metric-row" style={{ marginTop: 8 }}>
                    <span className="big">{fmtNum(d.open_exceptions)}</span>
                    <span className="delta bad">open exceptions</span>
                    <span className="meta" style={{ color: "var(--text-light)", fontSize: 12 }}>
                      {fmtNum(d.active_checks)} active checks
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-pad" style={{ paddingBottom: 0 }}>
          <div className="section-title" style={{ margin: 0 }}>
            <h2 style={{ fontSize: 14 }}>Recent runs</h2>
            <Link to="/runs" className="btn small">All runs</Link>
          </div>
        </div>
        <RunsTable runs={data.recent_runs} />
      </div>
    </div>
  );
}
