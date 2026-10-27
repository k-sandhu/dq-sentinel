import { Link } from "react-router";
import type { Check, Run } from "../../api/types";
import { fmtDateTime } from "../../lib/format";
import "./monitorEvidence.css";

type EvidenceProps = { checks: Check[]; runs: Run[]; error: unknown; loading: boolean };
type Measurement = { value: string | null; threshold: string | null };

function numeric(value: unknown): string | null {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : null;
}

function measurement(check: Check, run: Run): Measurement {
  const metrics = run.metrics ?? {};
  if (check.check_type === "freshness") {
    const age = numeric(metrics.age_hours);
    const maximum = numeric(metrics.max_age_hours);
    return { value: age === null ? null : `${age} hours old`, threshold: maximum === null ? null : `Maximum ${maximum} hours` };
  }
  if (check.check_type === "row_count_anomaly") {
    const count = numeric(metrics.row_count);
    const lower = numeric(metrics.lower_bound);
    const upper = numeric(metrics.upper_bound);
    // Older sigma-based runs did not record the applied limit; current check
    // settings cannot establish the threshold used for a historical run.
    return { value: count === null ? null : `${count} rows`, threshold: lower === null || upper === null ? null : `${lower}–${upper} rows` };
  }
  if (check.check_type === "schema_contract") {
    const current = numeric(metrics.current_count);
    const expected = numeric(metrics.expected_count);
    return { value: current === null ? null : `${current} columns`, threshold: expected === null ? null : `${expected} expected columns` };
  }
  if (check.check_type === "schema_change") {
    const count = numeric(metrics.column_count);
    return { value: count === null ? null : `${count} columns`, threshold: typeof metrics.baseline === "string" ? `${metrics.baseline} baseline` : null };
  }
  if (check.check_type === "distribution_drift") {
    const score = numeric(metrics.score);
    const threshold = numeric(metrics.threshold);
    return { value: score === null ? null : `Score ${score}`, threshold: threshold === null ? null : `Threshold ${threshold}` };
  }
  return { value: null, threshold: null };
}

function EvidenceCard({ check, runs, limited }: { check: Check; runs: Run[]; limited: boolean }) {
  const history = runs
    .filter((run) => run.dataset_id === check.dataset_id && run.check_id === check.id)
    .sort((a, b) => b.id - a.id);
  const latest = history[0];
  const result = latest && measurement(check, latest);
  return (
    <article className="card monitor-evidence-card">
      <h3><Link to={`/checks/${check.id}`}>{check.name}</Link></h3>
      {limited ? <p>History unavailable: batch limit reached.</p> : !latest ? <p>No evaluations yet.</p> : (
        <>
          <p><strong>{latest.status === "error" ? "Evaluation error" : result?.value ?? "Metric unavailable"}</strong></p>
          <p>{latest.status === "error" ? "Latest evaluation did not produce a reliable metric." : result?.threshold ?? "Threshold or range unavailable"}</p>
          <p>Evaluated {fmtDateTime(latest.finished_at ?? latest.started_at)} · {latest.status}</p>
          {latest.status !== "error" && typeof latest.metrics?.note === "string" && <p>{latest.metrics.note}</p>}
          {history.length < 2 ? <p>Insufficient history for a trend.</p> : (
            <details>
              <summary>Recent history</summary>
              <ol>
                {history.map((run) => (
                  <li key={run.id}>
                    {fmtDateTime(run.finished_at ?? run.started_at)}: {run.status === "error" ? "Evaluation error" : measurement(check, run).value ?? "Metric unavailable"}
                  </li>
                ))}
              </ol>
            </details>
          )}
        </>
      )}
    </article>
  );
}

export function MonitorEvidence({ checks, runs, error, loading }: EvidenceProps) {
  const severity: Record<string, number> = { error: 0, fail: 1, warn: 2 };
  const ordered = checks.map ( (check, index) => {
    const latest = index < 100
      ? runs.filter((run) => run.dataset_id === check.dataset_id && run.check_id === check.id)
        .reduce<Run | undefined>((current, run) => !current || run.id > current.id ? run : current, undefined)
      : undefined;
    return { check, index, rank: latest ? severity[latest.status] ?? 3 : 3 };
  }).sort((a, b) => a.rank - b.rank || a.index - b.index);
  return (
    <section className="monitor-evidence" aria-label="Monitor evaluation evidence">
      <h2>Monitor evaluations</h2>
      {loading ? <p>Loading evaluation history…</p> : error ? <p role="alert">Evaluation history could not be loaded.</p> : checks.length === 0 ? <p>No managed monitors yet.</p> : (
        <div className="monitor-evidence-grid">
          {ordered.map(({ check, index }) => <EvidenceCard key={check.id} check={check} runs={runs} limited={index >= 100} />)}
        </div>
      )}
    </section>
  );
}
