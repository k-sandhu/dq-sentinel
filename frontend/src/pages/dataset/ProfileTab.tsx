import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { ColumnProfile, Exploration, Preview, Profile } from "../../api/types";
import { EmptyState, Spinner } from "../../components/ui";
import { fmtNum, fmtPct, fmtValue } from "../../lib/format";

function ColumnCard({ col, rowCount }: { col: ColumnProfile; rowCount: number }) {
  const maxCount = col.top_values[0]?.count ?? 1;
  return (
    <div className="card card-pad">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <h3 style={{ marginBottom: 2 }}>
          {col.name}{" "}
          {Object.keys(col.patterns).map((p) => (
            <span key={p} className="badge ai" style={{ marginLeft: 4 }}>{p}</span>
          ))}
        </h3>
        <span className="badge kind">{col.dtype}</span>
      </div>
      <div style={{ display: "flex", gap: 18, fontSize: 12.5, margin: "8px 0 10px", flexWrap: "wrap" }}>
        <span>
          <strong style={{ color: col.null_pct > 0 ? "var(--danger-dark)" : "var(--text-dark)" }}>{fmtPct(col.null_pct, 2)}</strong>{" "}
          null
        </span>
        <span><strong>{fmtNum(col.distinct_count)}</strong> distinct</span>
        {col.kind === "numeric" && col.mean != null && (
          <span>μ <strong>{fmtValue(col.mean)}</strong> σ {fmtValue(col.stddev)}</span>
        )}
        {col.min != null && (
          <span>
            <strong>{fmtValue(col.min)}</strong> → <strong>{fmtValue(col.max)}</strong>
          </span>
        )}
        {col.avg_len != null && <span>len {col.min_len}–{col.max_len}</span>}
      </div>
      <div className="mini-bar" title={`${fmtPct(1 - col.null_pct, 2)} populated`}>
        <div style={{ width: `${Math.max(2, (1 - col.null_pct) * 100)}%`, background: col.null_pct > 0.05 ? "var(--warn-strong)" : "var(--brand)" }} />
      </div>
      {col.top_values.length > 0 && (
        <div style={{ marginTop: 10 }}>
          {col.top_values.slice(0, 5).map((tv, i) => (
            <div className="top-value-row" key={i}>
              <span className="val" title={String(tv.value)}>{fmtValue(tv.value)}</span>
              <span className="bar mini-bar"><div style={{ width: `${(tv.count / maxCount) * 100}%` }} /></span>
              <span className="cnt">{fmtPct(tv.count / Math.max(rowCount, 1))}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ProfileTab({
  datasetId,
  profile,
  loading,
  onProfileNow,
  profiling,
}: {
  datasetId: number;
  profile: Profile | null;
  loading: boolean;
  onProfileNow?: () => void;
  profiling?: boolean;
}) {
  const [showPreview, setShowPreview] = useState(false);
  const preview = useQuery({
    queryKey: qk.preview.detail(datasetId),
    queryFn: () => api.get<Preview>(`/datasets/${datasetId}/preview?limit=25`),
    enabled: showPreview,
  });
  const exploration = useQuery({
    queryKey: qk.exploration.detail(datasetId),
    queryFn: () => api.get<Exploration>(`/datasets/${datasetId}/exploration`),
  });

  if (loading) return <Spinner label="Loading profile…" />;
  if (!profile) {
    return (
      <div className="card">
        <EmptyState title="Not profiled yet" hint="Profiling computes per-column statistics, infers formats and finds key/freshness candidates — the raw material for generated checks.">
          {onProfileNow && (
            <button className="primary" onClick={onProfileNow} disabled={profiling}>
              {profiling ? "Profiling…" : "Profile this dataset"}
            </button>
          )}
        </EmptyState>
      </div>
    );
  }

  const facts = profile.table_facts;
  const insights = exploration.data?.insights ?? [];

  return (
    <div>
      <div className="toolbar">
        <span className="badge">rows {fmtNum(profile.row_count)}</span>
        <span className="badge">stats from {fmtNum(profile.sampled_rows)}-row sample</span>
        {facts.pk_candidates?.map((pk) => (
          <span key={pk} className="badge" title="every value unique & non-null">🔑 {pk}</span>
        ))}
        {facts.temporal_columns?.map((t) => (
          <span key={t.name} className="badge" title={`newest: ${t.max}`}>🕒 {t.name}</span>
        ))}
        <div className="right">
          <button className="small" onClick={() => setShowPreview(!showPreview)}>
            {showPreview ? "Hide preview" : "Preview rows"}
          </button>
        </div>
      </div>

      {insights.length > 0 && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <h3>
            <span className="badge ai">AI</span> Exploration insights{" "}
            <span style={{ fontWeight: 400, color: "var(--text-light)", fontSize: 12 }}>
              ({exploration.data?.queries_run} queries run)
            </span>
          </h3>
          {insights.map((ins, i) => (
            <div className="insight" key={i}>
              <div className="t">
                {ins.title} <span className={`risk-${ins.risk}`} style={{ fontSize: 11.5 }}>({ins.risk} risk)</span>
              </div>
              <div style={{ fontSize: 12.5 }}>{ins.detail}</div>
            </div>
          ))}
        </div>
      )}

      {showPreview && (
        <div className="card table-wrap" style={{ marginBottom: 16 }}>
          {preview.isLoading ? (
            <Spinner />
          ) : preview.data ? (
            <table className="data">
              <thead>
                <tr>{preview.data.columns.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {preview.data.rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((v, j) => (
                      <td key={j} className="mono" style={{ whiteSpace: "nowrap", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis" }}>
                        {fmtValue(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      )}

      <div className="grid cols-3">
        {profile.columns.map((c) => (
          <ColumnCard key={c.name} col={c} rowCount={profile.row_count} />
        ))}
      </div>
    </div>
  );
}
