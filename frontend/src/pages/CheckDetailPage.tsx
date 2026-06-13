import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { api } from "../api/client";
import type { Check, ExceptionRecord, Run } from "../api/types";
import { canEdit, useAuth } from "../auth";
import Breadcrumbs from "../components/Breadcrumbs";
import RunsTable from "../components/RunsTable";
import { EmptyState, ErrorBox, Icon, Pill, SeverityDot, Spinner, StatCard } from "../components/ui";
import { checkTypeLabel, originLabel } from "../lib/checkMeta";
import { describeSchedule, fmtDateTime, fmtNum, fmtValue, timeAgo } from "../lib/format";

function MiniRunTrend({ runs }: { runs: Run[] }) {
  const recent = runs.slice(0, 20).reverse();
  if (!recent.length) return <div className="field-hint">No run history yet.</div>;
  return (
    <div className="mini-run-trend" aria-label="Recent run status trend">
      {recent.map((run) => (
        <Link
          key={run.id}
          to={`/runs/${run.id}`}
          className={`trend-block ${run.status}`}
          title={`Run #${run.id}: ${run.status}, ${run.violation_count} violations`}
        />
      ))}
    </div>
  );
}

export default function CheckDetailPage() {
  const { id } = useParams();
  const checkId = Number(id);
  const { user } = useAuth();
  const qc = useQueryClient();

  const checksQuery = useQuery({
    queryKey: ["checks"],
    queryFn: () => api.get<Check[]>("/checks"),
  });
  const runsQuery = useQuery({
    queryKey: ["runs", { checkId }],
    queryFn: () => api.get<Run[]>(`/runs?check_id=${checkId}&limit=100`),
  });
  const exceptionsQuery = useQuery({
    queryKey: ["exceptions", { checkId }],
    queryFn: () => api.get<ExceptionRecord[]>(`/exceptions?check_id=${checkId}&limit=20`),
  });

  const runNow = useMutation({
    mutationFn: () => api.post<Run>(`/checks/${checkId}/run`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["checks"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const check = checksQuery.data?.find((c) => c.id === checkId);
  if (checksQuery.isLoading) return <Spinner label="Loading check..." />;

  return (
    <div className="page">
      <Breadcrumbs items={[{ label: "Checks", to: "/checks" }, { label: check?.name ?? `Check #${checkId}` }]} />
      <ErrorBox error={checksQuery.error || runsQuery.error || exceptionsQuery.error || runNow.error} />
      {!check ? (
        <div className="card">
          <EmptyState title="Check not found" hint="Archived checks are hidden from the global checks list." />
        </div>
      ) : (
        <>
          <div className="page-header">
            <div>
              <h1>
                {check.name} <Pill value={check.status} />
              </h1>
              <div className="sub">
                <Link to={`/datasets/${check.dataset_id}/checks`}>{check.dataset_name}</Link> ·{" "}
                {checkTypeLabel(check.check_type)}
                {check.column_name ? ` · ${check.column_name}` : ""}
              </div>
            </div>
            <div className="header-actions">
              <Link to={`/workbench?dataset_id=${check.dataset_id}&check_id=${check.id}`} className="btn">
                <Icon name="search" size={13} /> Investigate
              </Link>
              {canEdit(user) && (
                <button onClick={() => runNow.mutate()} disabled={runNow.isPending}>
                  <Icon name="play" size={13} /> {runNow.isPending ? "Running..." : "Run now"}
                </button>
              )}
            </div>
          </div>

          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <StatCard label="Last result" value={check.last_status ? <Pill value={check.last_status} /> : "Never"} />
            <StatCard label="Severity" value={<SeverityDot severity={check.severity} />} />
            <StatCard label="Schedule" value={describeSchedule(check.schedule_kind, check.schedule_expr)} />
            <StatCard label="Last run" value={timeAgo(check.last_run_at)} />
          </div>

          <div className="grid cols-2">
            <div className="card card-pad">
              <h3>Configuration</h3>
              <dl className="detail-list">
                <dt>Type</dt>
                <dd>{checkTypeLabel(check.check_type)}</dd>
                <dt>Column</dt>
                <dd>{check.column_name ? <code>{check.column_name}</code> : "Table-level"}</dd>
                <dt>Origin</dt>
                <dd><span className={`badge ${check.origin === "llm" ? "ai" : ""}`}>{originLabel(check.origin)}</span></dd>
                <dt>Created</dt>
                <dd>{fmtDateTime(check.created_at)}</dd>
              </dl>
              {check.rationale && <div className="info-box">{check.rationale}</div>}
            </div>
            <div className="card card-pad">
              <h3>Params</h3>
              <pre className="result">{JSON.stringify(check.params ?? {}, null, 2)}</pre>
            </div>
          </div>

          <div className="section-title">
            <h2>Violation Trend</h2>
            <span className="field-hint">{fmtNum(runsQuery.data?.length ?? 0)} recent runs</span>
          </div>
          <div className="card card-pad">
            <MiniRunTrend runs={runsQuery.data ?? []} />
          </div>

          <div className="section-title">
            <h2>Run History</h2>
          </div>
          <div className="card">
            <RunsTable runs={runsQuery.data ?? []} showDataset={false} />
          </div>

          <div className="section-title">
            <h2>Recent Exceptions</h2>
            <Link to={`/exceptions?check_id=${check.id}`}>Open in triage →</Link>
          </div>
          {!exceptionsQuery.data?.length ? (
            <div className="card">
              <EmptyState title="No exceptions found for this check" />
            </div>
          ) : (
            <div className="card table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Reason</th>
                    <th>Row data</th>
                    <th>Run</th>
                  </tr>
                </thead>
                <tbody>
                  {exceptionsQuery.data.map((exc) => (
                    <tr key={exc.id}>
                      <td><Pill value={exc.status} /></td>
                      <td style={{ fontWeight: 600, color: "var(--text-dark)" }}>{exc.reason}</td>
                      <td>
                        <div className="rowdata">
                          {Object.entries(exc.row_data).slice(0, 5).map(([k, v]) => `${k}=${fmtValue(v)}`).join("  ")}
                        </div>
                      </td>
                      <td><Link to={`/runs/${exc.run_id}`}>#{exc.run_id}</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
