// Reliability dashboard (#102): SLA attainment, error budgets, MTTR and a
// per-SLA attainment trend. Editors can define dataset SLAs and re-evaluate.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Dataset, Reliability, Sla, SlaDetail, SLAEvaluation } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { ErrorBox, Icon, Spinner } from "../components/ui";
import { timeAgo } from "../lib/format";

function pct(x: number): string {
  return `${(x * 100).toFixed(2)}%`;
}

function fmtDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

function attainmentTone(sla: Sla): string {
  if (!sla.latest) return "neutral";
  if (sla.latest.breached) return "danger";
  // within 1 budget-doubling of the objective → warn
  return sla.latest.budget_consumed >= 0.5 ? "warn" : "ok";
}

// Dependency-free attainment sparkline (matches the app's hand-rolled-SVG style).
function Sparkline({ evals, objective }: { evals: SLAEvaluation[]; objective: number }) {
  if (evals.length < 2) return <span className="sub">not enough history yet</span>;
  const w = 280;
  const h = 48;
  const xs = evals.map((_, i) => (i / (evals.length - 1)) * w);
  const ys = evals.map((e) => h - e.attainment * h);
  const pts = xs.map((x, i) => `${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  const objY = h - objective * h;
  return (
    <svg width={w} height={h} style={{ overflow: "visible" }} role="img" aria-label="Attainment trend">
      <line x1={0} y1={objY} x2={w} y2={objY} stroke="var(--warn-strong, #c77)" strokeDasharray="3 3" strokeWidth={1} />
      <polyline points={pts} fill="none" stroke="var(--brand)" strokeWidth={2} />
    </svg>
  );
}

function SlaCard({ sla }: { sla: Sla }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const detail = useQuery({
    queryKey: ["sla", sla.id],
    queryFn: () => api.get<SlaDetail>(`/sla/${sla.id}`),
    enabled: open,
  });
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["reliability"] });
    qc.invalidateQueries({ queryKey: ["sla", sla.id] });
  };
  const evaluate = useMutation({
    mutationFn: () => api.post<Sla>(`/sla/${sla.id}/evaluate`),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => api.del<void>(`/sla/${sla.id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reliability"] }),
  });

  const l = sla.latest;
  const tone = attainmentTone(sla);
  return (
    <div className="card card-pad" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
        <div>
          <div style={{ fontWeight: 700, color: "var(--text-dark)" }}>{sla.name}</div>
          <div className="sub">
            {sla.scope_label} · {sla.target_type} · {sla.window.replace("rolling_", "")} · objective {pct(sla.objective)}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 26, fontWeight: 800, color: `var(--${tone === "ok" ? "ok" : tone === "warn" ? "warn-strong" : tone === "danger" ? "danger" : "text"})` }}>
            {l ? pct(l.attainment) : "—"}
          </div>
          {l?.breached && <span className="pill tone-danger">breached</span>}
        </div>
      </div>

      <div className="chip-row" style={{ marginTop: 10 }}>
        <span className="pill tone-neutral">{l ? `${l.good} good / ${l.bad} bad` : "no runs"}</span>
        <span className="pill tone-neutral">budget {l ? `${Math.round(Math.min(1, l.budget_consumed) * 100)}%` : "—"}</span>
        <span className="pill tone-neutral">MTTR {fmtDuration(l?.mttr_seconds ?? null)}</span>
        {!sla.enabled && <span className="pill tone-warn">disabled</span>}
        <span className="sub" style={{ alignSelf: "center" }}>
          {l ? `evaluated ${timeAgo(l.evaluated_at)}` : ""}
        </span>
      </div>

      <div className="toolbar" style={{ marginTop: 10 }}>
        <button className="btn small ghost" onClick={() => setOpen((v) => !v)}>
          <Icon name={open ? "up" : "down"} size={12} /> {open ? "Hide trend" : "Show trend"}
        </button>
        <div className="right">
          {canEdit(user) && (
            <>
              <button className="btn small" onClick={() => evaluate.mutate()} disabled={evaluate.isPending}>
                <Icon name="refresh" size={12} /> Re-evaluate
              </button>
              <button
                className="btn small ghost"
                onClick={() => window.confirm(`Delete SLA “${sla.name}”?`) && remove.mutate()}
                disabled={remove.isPending}
              >
                <Icon name="x" size={12} /> Delete
              </button>
            </>
          )}
        </div>
      </div>
      <ErrorBox error={evaluate.error || remove.error} />

      {open && (
        <div style={{ marginTop: 10 }}>
          {detail.isLoading && <Spinner label="Loading trend…" />}
          {detail.data && (
            <>
              <Sparkline evals={detail.data.evaluations} objective={sla.objective} />
              <div className="sub" style={{ marginTop: 4 }}>
                Attainment over the last {detail.data.evaluations.length} evaluation(s); dashed line = objective.
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function NewSlaForm() {
  const qc = useQueryClient();
  const [datasetId, setDatasetId] = useState<number | "">("");
  const [name, setName] = useState("");
  const [targetType, setTargetType] = useState<"check_success" | "freshness" | "volume">("check_success");
  const [objectivePct, setObjectivePct] = useState(99);
  const [window, setWindow] = useState<"rolling_7d" | "rolling_30d">("rolling_30d");

  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.get<Dataset[]>("/datasets") });
  const create = useMutation({
    mutationFn: () =>
      api.post<Sla>("/sla", {
        name,
        scope: "dataset",
        scope_id: datasetId,
        target_type: targetType,
        objective: Math.min(1, Math.max(0.01, objectivePct / 100)),
        window,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reliability"] });
      setName("");
      setDatasetId("");
    },
  });

  return (
    <div className="card card-pad" style={{ marginBottom: 16 }}>
      <div style={{ fontWeight: 700, color: "var(--text-dark)", marginBottom: 8 }}>New SLA</div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 12 }}>
          <span className="sub">Dataset</span>
          <select value={datasetId} onChange={(e) => setDatasetId(e.target.value ? Number(e.target.value) : "")}>
            <option value="">Select…</option>
            {(datasets.data ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.schema_name ? `${d.schema_name}.` : ""}
                {d.table_name}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 12 }}>
          <span className="sub">Target</span>
          <select value={targetType} onChange={(e) => setTargetType(e.target.value as typeof targetType)}>
            <option value="check_success">All checks pass</option>
            <option value="freshness">Freshness</option>
            <option value="volume">Volume</option>
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 12 }}>
          <span className="sub">Objective %</span>
          <input
            type="number"
            min={1}
            max={100}
            step={0.1}
            value={objectivePct}
            onChange={(e) => setObjectivePct(Number(e.target.value))}
            style={{ width: 90 }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 12 }}>
          <span className="sub">Window</span>
          <select value={window} onChange={(e) => setWindow(e.target.value as typeof window)}>
            <option value="rolling_7d">Rolling 7 days</option>
            <option value="rolling_30d">Rolling 30 days</option>
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 12, flex: 1, minWidth: 160 }}>
          <span className="sub">Name (optional)</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Orders freshness" />
        </label>
        <button onClick={() => create.mutate()} disabled={!datasetId || create.isPending}>
          {create.isPending ? "Creating…" : "Create SLA"}
        </button>
      </div>
      <ErrorBox error={create.error} />
    </div>
  );
}

export default function ReliabilityPage() {
  const { user } = useAuth();
  const { data, isLoading, error } = useQuery({
    queryKey: ["reliability"],
    queryFn: () => api.get<Reliability>("/sla/reliability"),
  });

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Reliability</h1>
          <div className="sub">SLA attainment, error budgets and time-to-resolve across your datasets.</div>
        </div>
        {data && (
          <div className="chip-row" style={{ alignItems: "center" }}>
            <span className="pill tone-neutral">{data.total} SLAs</span>
            <span className={`pill tone-${data.breached ? "danger" : "ok"}`}>{data.breached} breached</span>
          </div>
        )}
      </div>
      <ErrorBox error={error} />
      {canEdit(user) && <NewSlaForm />}
      {isLoading && <Spinner label="Loading SLAs…" />}
      {data && data.slas.length === 0 && (
        <div className="card card-pad sub">
          No SLAs yet. {canEdit(user) ? "Create one above" : "Ask an editor to define one"} — or set a
          dataset's freshness SLA in its Knowledge tab and it'll be tracked here automatically.
        </div>
      )}
      {data?.slas.map((s) => (
        <SlaCard key={s.id} sla={s} />
      ))}
    </div>
  );
}
