import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { Dashboard } from "../api/types";
import RunsTable from "../components/RunsTable";
import { ErrorBox, Pill, Spinner, StatCard } from "../components/ui";
import { fmtNum, fmtPct } from "../lib/format";

export default function HomePage() {
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
            )}
          </div>
        </div>
        <div className="header-actions">
          <Link to="/connections" className="btn">Add data</Link>
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

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <div className="card card-pad">
          <h3>Run results — last 14 days</h3>
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={trend} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
              <XAxis dataKey="day" tick={{ fontSize: 11, fill: "#949aab" }} tickLine={false} axisLine={{ stroke: "#e3e7e9" }} />
              <YAxis tick={{ fontSize: 11, fill: "#949aab" }} tickLine={false} axisLine={false} allowDecimals={false} />
              <Tooltip cursor={{ fill: "#f5f9fd" }} contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e3e7e9" }} />
              <Bar dataKey="passed" stackId="a" fill="#84bb4c" />
              <Bar dataKey="warned" stackId="a" fill="#f7c844" />
              <Bar dataKey="failed" stackId="a" fill="#ed6e6e" />
              <Bar dataKey="errored" stackId="a" fill="#a33" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card card-pad">
          <h3>Datasets needing attention</h3>
          {data.worst_datasets.length === 0 ? (
            <div className="empty" style={{ padding: 24 }}>No open exceptions anywhere. 🎉</div>
          ) : (
            <table className="data">
              <tbody>
                {data.worst_datasets.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <Link to={`/datasets/${d.id}/exceptions`} style={{ fontWeight: 700 }}>
                        {d.table_name}
                      </Link>
                      <div style={{ fontSize: 11.5, color: "var(--text-light)" }}>{d.connection_name}</div>
                    </td>
                    <td><Pill value={d.health} /></td>
                    <td className="num" style={{ color: "var(--danger-dark)", fontWeight: 700 }}>
                      {fmtNum(d.open_exceptions)} open
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-pad" style={{ paddingBottom: 0 }}>
          <h3>Recent runs</h3>
        </div>
        <RunsTable runs={data.recent_runs} />
      </div>
    </div>
  );
}
