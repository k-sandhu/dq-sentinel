import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, ApiError } from "../api/client";
import { qk } from "../api/queryKeys";
import type { Check, Run } from "../api/types";
import { canEdit, useAuth } from "../auth";
import CheckHistory from "../components/CheckHistory";
import RunsTable from "../components/RunsTable";
import { EmptyState, ErrorBox, Icon, NotFoundState, SeverityBadge, Spinner, StatCard, StatusPill } from "../components/ui";
import { checkTypeLabel, originLabel } from "../lib/checkMeta";
import { describeSchedule, fmtDateTime, fmtNum, isOverdue, timeAgo } from "../lib/format";

const TOOLTIP_STYLE = {
  fontSize: 12,
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--card)",
  color: "var(--text-dark)",
};

function paramsSummary(params: Record<string, unknown>): string {
  const entries = Object.entries(params ?? {});
  if (!entries.length) return "No params";
  return entries
    .map(([k, v]) => `${k}=${Array.isArray(v) || typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(", ");
}

export default function CheckDetailPage() {
  const { id } = useParams();
  const checkId = Number(id);
  const { user } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const editable = canEdit(user);

  // Single-check fetch (perf): this page previously downloaded the full checks
  // list — hundreds of rows with params JSON — just to find one row.
  const checksQuery = useQuery({
    queryKey: qk.checkDetail.detail(checkId),
    queryFn: () => api.get<Check>(`/checks/${checkId}`),
    enabled: Number.isFinite(checkId),
  });
  const check = checksQuery.data ?? null;

  const runsQuery = useQuery({
    queryKey: ["runs", { checkId }],
    queryFn: () => api.get<Run[]>(`/runs?check_id=${checkId}&limit=100`),
    enabled: Number.isFinite(checkId),
    refetchInterval: 20_000,
  });

  const runNow = useMutation({
    mutationFn: () => api.post<Run>(`/checks/${checkId}/run`),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["checks"] });
      qc.invalidateQueries({ queryKey: qk.checkDetail.all });
      qc.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run.id}`);
    },
  });

  // The detail endpoint 404s for archived/deleted ids — same "not found" render
  // the list-scan produced before, not a raw error box.
  const notFound =
    checksQuery.error instanceof ApiError && checksQuery.error.status === 404;
  if (checksQuery.isLoading) return <Spinner label="Loading check..." />;
  if (checksQuery.error && !notFound)
    return <div className="page"><ErrorBox error={checksQuery.error} /></div>;
  if (!check) {
    return <NotFoundState what="Check" backTo="/checks" backLabel="Back to checks" />;
  }

  const runs = runsQuery.data ?? [];
  const latest = runs[0];
  const trend = runs
    .slice()
    .reverse()
    .map((r) => ({
      run: `#${r.id}`,
      violations: r.violation_count,
      exceptions: r.exception_count,
      status: r.status,
    }));

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>
            {check.name} <StatusPill value={check.status} />
          </h1>
          <div className="sub">
            <Link to={`/datasets/${check.dataset_id}/checks`}>{check.dataset_name}</Link> ·{" "}
            {checkTypeLabel(check.check_type)}
            {check.column_name ? <> on <code>{check.column_name}</code></> : ""} · {describeSchedule(check.schedule_kind, check.schedule_expr)}
          </div>
        </div>
        <div className="header-actions">
          <Link to={`/datasets/${check.dataset_id}/checks`} className="btn">
            <Icon name="table" size={13} /> Dataset checks
          </Link>
          <Link to={`/workbench?dataset_id=${check.dataset_id}`} className="btn">
            <Icon name="search" size={13} /> Workbench
          </Link>
          {editable && (
            <button className="primary" onClick={() => runNow.mutate()} disabled={runNow.isPending}>
              {runNow.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="play" size={13} />}
              Run now
            </button>
          )}
        </div>
      </div>

      <ErrorBox error={runsQuery.error || runNow.error} />

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <StatCard label="Latest status" value={<StatusPill value={check.last_status ?? "unknown"} />} hint={timeAgo(check.last_run_at)} />
        <StatCard label="Runs loaded" value={fmtNum(runs.length)} />
        <StatCard label="Latest violations" value={fmtNum(latest?.violation_count)} tone={latest?.violation_count ? "danger" : "ok"} />
        {/* A "next run" in the past means the scheduler is idle or behind —
            say so instead of presenting a stale date as a plan (UX P2). */}
        {check.status === "active" && isOverdue(check.next_run_at) ? (
          <StatCard
            label="Next run"
            value={<span style={{ fontSize: 18 }}>overdue</span>}
            tone="danger"
            hint={`was due ${timeAgo(check.next_run_at)} — scheduler idle or behind`}
            title={fmtDateTime(check.next_run_at)}
          />
        ) : (
          <StatCard label="Next run" value={<span style={{ fontSize: 18 }}>{fmtDateTime(check.next_run_at)}</span>} />
        )}
      </div>

      <div className="split" style={{ marginBottom: 16 }}>
        <div className="card card-pad">
          <h3>Configuration</h3>
          <div className="table-wrap">
            <table className="data">
              <tbody>
                <tr>
                  <td style={{ fontWeight: 700, width: 150 }}>Type</td>
                  <td>{checkTypeLabel(check.check_type)}</td>
                </tr>
                <tr>
                  <td style={{ fontWeight: 700 }}>Severity</td>
                  <td><SeverityBadge severity={check.severity} /></td>
                </tr>
                <tr>
                  <td style={{ fontWeight: 700 }}>Origin</td>
                  <td><span className={`badge ${check.origin === "llm" ? "ai" : ""}`}>{originLabel(check.origin)}</span></td>
                </tr>
                <tr>
                  <td style={{ fontWeight: 700 }}>Params</td>
                  <td className="mono" style={{ overflowWrap: "anywhere" }}>{paramsSummary(check.params)}</td>
                </tr>
                {check.rationale && (
                  <tr>
                    <td style={{ fontWeight: 700 }}>Rationale</td>
                    <td>{check.rationale}</td>
                  </tr>
                )}
                <tr>
                  <td style={{ fontWeight: 700 }}>Created</td>
                  <td>{fmtDateTime(check.created_at)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div className="card card-pad">
          <h3>Violation trend</h3>
          {trend.length ? (
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={trend} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
                <XAxis dataKey="run" tick={{ fontSize: 11, fill: "var(--text-light)" }} tickLine={false} axisLine={{ stroke: "var(--border)" }} />
                <YAxis tick={{ fontSize: 11, fill: "var(--text-light)" }} tickLine={false} axisLine={false} allowDecimals={false} />
                <Tooltip cursor={{ fill: "var(--hover)" }} contentStyle={TOOLTIP_STYLE} />
                <Bar dataKey="violations" fill="var(--danger)" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState title="No run history yet" hint="Run this check to start building a trend." />
          )}
        </div>
      </div>

      {typeof check.params?.sql === "string" && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <h3>Violation SQL</h3>
          <pre className="sql">{check.params.sql}</pre>
        </div>
      )}

      <div className="section-title">
        <h2>Version history</h2>
      </div>
      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <CheckHistory checkId={check.id} />
      </div>

      <div className="section-title">
        <h2>Run history</h2>
        <Link to={`/runs?check_id=${check.id}`} className="btn small">
          All runs
        </Link>
      </div>
      {runsQuery.isLoading ? <Spinner /> : <RunsTable runs={runs} showDataset={false} />}
    </div>
  );
}
