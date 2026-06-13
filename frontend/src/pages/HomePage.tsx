import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { Dashboard } from "../api/types";
import RunsTable from "../components/RunsTable";
import { EmptyState, ErrorBox, Icon, Spinner, StatCard, StatusPill } from "../components/ui";
import { fmtNum, fmtPct } from "../lib/format";

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

  const trend = data.trend.map((t) => ({ ...t, day: t.day.slice(5) }));

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
            <span className="badge">{fmtNum(data.runs_24h)} runs in 24h</span>{" "}
            {data.pass_rate_7d != null && (
              <span className="badge">7-day pass rate {fmtPct(data.pass_rate_7d)}</span>
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
        <StatCard label="Datasets monitored" value={fmtNum(data.datasets)} />
        <StatCard
          label="Active checks"
          value={fmtNum(data.active_checks)}
          hint={data.proposed_checks ? `${data.proposed_checks} proposals awaiting review` : undefined}
        />
        <StatCard
          label="Failing checks"
          value={fmtNum(data.failing_checks)}
          tone={data.failing_checks ? "danger" : "ok"}
          hint={`${fmtNum(data.runs_24h)} runs in 24h`}
        />
        <StatCard
          label="Open exceptions"
          value={fmtNum(data.open_exceptions)}
          tone={data.open_exceptions ? "danger" : "ok"}
          hint={data.pass_rate_7d != null ? `7-day pass rate ${fmtPct(data.pass_rate_7d)}` : undefined}
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
          <ResponsiveContainer width="100%" height={210}>
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
                  onClick={() => navigate(`/datasets/${d.id}/exceptions`)}
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
