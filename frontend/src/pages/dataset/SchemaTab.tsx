// Dataset "Schema" tab (#101): the column schema plus a change-history timeline.
// Snapshots are captured (deduped) on profiling and on schema_change check runs;
// "Pin current as baseline" sets the baseline that baseline=pinned checks enforce.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { SchemaHistory, SchemaSnapshot } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import { ErrorBox, Icon, Spinner } from "../../components/ui";
import { timeAgo } from "../../lib/format";

function ChangeChips({ snap }: { snap: SchemaSnapshot }) {
  const s = snap.change_summary;
  if (!s) return <span className="sub">initial snapshot</span>;
  const chips: { label: string; tone: string }[] = [];
  if (s.added.length) chips.push({ label: `+${s.added.length} added`, tone: "ok" });
  if (s.removed.length) chips.push({ label: `−${s.removed.length} removed`, tone: "danger" });
  if (s.type_changed) chips.push({ label: `${s.type_changed} retyped`, tone: "warn" });
  if (s.nullability_changed) chips.push({ label: `${s.nullability_changed} nullability`, tone: "warn" });
  if (s.reordered) chips.push({ label: "reordered", tone: "neutral" });
  if (!chips.length) return <span className="sub">no change</span>;
  return (
    <div className="chip-row" style={{ marginTop: 4 }}>
      {chips.map((c) => (
        <span key={c.label} className={`pill tone-${c.tone}`}>
          {c.label}
        </span>
      ))}
    </div>
  );
}

export default function SchemaTab({ datasetId }: { datasetId: number }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["schema-history", datasetId],
    queryFn: () => api.get<SchemaHistory>(`/datasets/${datasetId}/schema-history`),
  });
  const pin = useMutation({
    mutationFn: () => api.post<SchemaSnapshot>(`/datasets/${datasetId}/schema-baseline`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schema-history", datasetId] }),
  });

  if (isLoading) return <Spinner label="Loading schema history…" />;

  const snaps = data?.snapshots ?? [];
  const pinnedId = data?.pinned_baseline_id ?? null;
  const current = (selected != null && snaps.find((s) => s.id === selected)) || snaps[0] || null;

  return (
    <div className="card card-pad">
      <div className="toolbar">
        <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--text-dark)" }}>Schema history</span>
        <div className="right">
          {canEdit(user) && (
            <button className="btn small" onClick={() => pin.mutate()} disabled={pin.isPending}>
              <Icon name="star" size={12} /> {pin.isPending ? "Pinning…" : "Pin current as baseline"}
            </button>
          )}
        </div>
      </div>
      <ErrorBox error={error || pin.error} />

      {snaps.length === 0 ? (
        <p className="sub" style={{ marginTop: 8 }}>
          No schema captured yet. Profile this dataset, or run a <code>schema_change</code> check, to
          start the timeline.
        </p>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: 16, marginTop: 6 }}>
          <div>
            <div className="sub" style={{ marginBottom: 6 }}>
              {snaps.length} version{snaps.length === 1 ? "" : "s"}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {snaps.map((s) => {
                const on = current?.id === s.id;
                return (
                  <button
                    key={s.id}
                    onClick={() => setSelected(s.id)}
                    style={{
                      textAlign: "left",
                      padding: "8px 10px",
                      borderRadius: 8,
                      border: `1px solid ${on ? "var(--brand)" : "var(--border)"}`,
                      background: on ? "var(--brand-faint, var(--bg))" : "var(--card)",
                      cursor: "pointer",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                      <span style={{ fontWeight: 600, color: "var(--text-dark)" }}>{timeAgo(s.captured_at)}</span>
                      <span className="sub" style={{ display: "flex", gap: 4, alignItems: "center" }}>
                        {s.id === pinnedId && <span title="Pinned baseline">★</span>}
                        <span className="badge kind">{s.source}</span>
                      </span>
                    </div>
                    <ChangeChips snap={s} />
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            {current && (
              <>
                <div className="sub" style={{ marginBottom: 8 }}>
                  {current.columns.length} columns · captured {timeAgo(current.captured_at)}
                  {current.id === pinnedId ? " · pinned baseline" : ""}
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th style={{ width: 36 }}>#</th>
                        <th>Column</th>
                        <th>Type</th>
                        <th style={{ width: 90 }}>Nullable</th>
                      </tr>
                    </thead>
                    <tbody>
                      {current.columns.map((c) => (
                        <tr key={c.name}>
                          <td className="sub">{c.ordinal}</td>
                          <td style={{ fontFamily: "var(--mono)" }}>{c.name}</td>
                          <td style={{ fontFamily: "var(--mono)" }}>{c.dtype}</td>
                          <td>{c.nullable ? "yes" : "no"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
