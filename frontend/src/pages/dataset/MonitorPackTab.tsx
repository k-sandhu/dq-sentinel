import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Check, MonitorKind, MonitorPackConfig, MonitorPackSkipped, MonitorPackState } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import { checkTypeLabel } from "../../lib/checkMeta";
import { describeSchedule, fmtDateTime, timeAgo } from "../../lib/format";
import { EmptyState, ErrorBox, Icon, Modal, SeverityBadge, Spinner, StatusPill } from "../../components/ui";

const MONITOR_KINDS: { key: MonitorKind; label: string; hint: string }[] = [
  { key: "freshness", label: "Freshness", hint: "Late or stale arrivals against the table's temporal column." },
  { key: "volume", label: "Volume", hint: "Unexpected row-count movement between runs." },
  { key: "schema", label: "Schema", hint: "Added, removed, or changed columns compared with the baseline." },
  { key: "drift", label: "Drift", hint: "Distribution shifts in profiled columns." },
];

const DEFAULT_CONFIG: MonitorPackConfig = {
  version: 1,
  monitors: { freshness: true, volume: true, schema: true, drift: true },
  cadence: {
    freshness_minutes: 360,
    volume_minutes: 1440,
    schema_minutes: 360,
    drift_minutes: 1440,
  },
  sensitivity: {
    freshness_max_age_hours: 48,
    volume_sigma: 3,
    volume_lookback_runs: 14,
    volume_min_history: 5,
    drift_threshold: 0.2,
  },
  limits: { max_drift_checks: 4 },
  overrides: {},
};

function mergeConfig(config: MonitorPackConfig | null | undefined): MonitorPackConfig {
  return {
    ...DEFAULT_CONFIG,
    ...(config ?? {}),
    monitors: { ...DEFAULT_CONFIG.monitors, ...(config?.monitors ?? {}) },
    cadence: { ...DEFAULT_CONFIG.cadence, ...(config?.cadence ?? {}) },
    sensitivity: { ...DEFAULT_CONFIG.sensitivity, ...(config?.sensitivity ?? {}) },
    limits: { ...(DEFAULT_CONFIG.limits ?? {}), ...(config?.limits ?? {}) },
    overrides: { ...(DEFAULT_CONFIG.overrides ?? {}), ...(config?.overrides ?? {}) },
  };
}

function monitorKind(check: Check): MonitorKind | "unknown" {
  const meta = (check.params?.monitor_pack ?? {}) as { kind?: unknown };
  return typeof meta.kind === "string" && MONITOR_KINDS.some((k) => k.key === meta.kind)
    ? (meta.kind as MonitorKind)
    : "unknown";
}

function packStatus(pack: MonitorPackState | undefined): string {
  if (!pack) return "unknown";
  if (!pack.enabled) return "disabled";
  return pack.status === "ready" ? "active" : pack.status;
}

function cadenceLabel(config: MonitorPackConfig): string {
  const c = mergeConfig(config).cadence;
  return MONITOR_KINDS.map((k) => `${k.label.toLowerCase()} ${describeSchedule("interval", String(c[`${k.key}_minutes`] ?? 1440))}`).join(", ");
}

function MonitorConfigModal({
  pack,
  saving,
  error,
  onSave,
  onClose,
}: {
  pack: MonitorPackState;
  saving: boolean;
  error: unknown;
  onSave: (config: MonitorPackConfig) => void;
  onClose: () => void;
}) {
  const initial = mergeConfig(pack.config);
  const [monitors, setMonitors] = useState(initial.monitors);
  const [cadence, setCadence] = useState<Record<MonitorKind, string>>({
    freshness: String(initial.cadence.freshness_minutes ?? 360),
    volume: String(initial.cadence.volume_minutes ?? 1440),
    schema: String(initial.cadence.schema_minutes ?? 360),
    drift: String(initial.cadence.drift_minutes ?? 1440),
  });
  const [freshnessHours, setFreshnessHours] = useState(String(initial.sensitivity.freshness_max_age_hours ?? 48));
  const [volumeSigma, setVolumeSigma] = useState(String(initial.sensitivity.volume_sigma ?? 3));
  const [driftThreshold, setDriftThreshold] = useState(String(initial.sensitivity.drift_threshold ?? 0.2));
  const [maxDriftChecks, setMaxDriftChecks] = useState(String((initial.limits ?? {}).max_drift_checks ?? 4));

  const submit = () => {
    onSave({
      ...initial,
      monitors,
      cadence: {
        ...initial.cadence,
        freshness_minutes: Number(cadence.freshness),
        volume_minutes: Number(cadence.volume),
        schema_minutes: Number(cadence.schema),
        drift_minutes: Number(cadence.drift),
      },
      sensitivity: {
        ...initial.sensitivity,
        freshness_max_age_hours: Number(freshnessHours),
        volume_sigma: Number(volumeSigma),
        drift_threshold: Number(driftThreshold),
      },
      limits: {
        ...(initial.limits ?? {}),
        max_drift_checks: Number(maxDriftChecks),
      },
    });
  };

  return (
    <Modal
      title="Configure monitor pack"
      onClose={onClose}
      dirty
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={submit} disabled={saving}>
            Save settings
          </button>
        </>
      }
    >
      <ErrorBox error={error} />
      <div className="monitor-kind-controls">
        {MONITOR_KINDS.map((kind) => (
          <label key={kind.key} className="monitor-kind-toggle">
            <input
              type="checkbox"
              checked={monitors[kind.key]}
              onChange={(e) => setMonitors((cur) => ({ ...cur, [kind.key]: e.target.checked }))}
            />
            <span>
              <strong>{kind.label}</strong>
              <small>{kind.hint}</small>
            </span>
          </label>
        ))}
      </div>
      <div className="form-row">
        {MONITOR_KINDS.map((kind) => (
          <label key={kind.key} className="field">
            {kind.label} cadence minutes
            <input
              type="number"
              min={5}
              value={cadence[kind.key]}
              onChange={(e) => setCadence((cur) => ({ ...cur, [kind.key]: e.target.value }))}
            />
          </label>
        ))}
      </div>
      <div className="form-row">
        <label className="field">
          Freshness max age hours
          <input type="number" min={1} value={freshnessHours} onChange={(e) => setFreshnessHours(e.target.value)} />
        </label>
        <label className="field">
          Volume sigma
          <input type="number" min={0.1} step={0.1} value={volumeSigma} onChange={(e) => setVolumeSigma(e.target.value)} />
        </label>
      </div>
      <div className="form-row">
        <label className="field">
          Drift threshold
          <input type="number" min={0.01} step={0.01} value={driftThreshold} onChange={(e) => setDriftThreshold(e.target.value)} />
        </label>
        <label className="field">
          Max drift checks
          <input type="number" min={0} max={20} value={maxDriftChecks} onChange={(e) => setMaxDriftChecks(e.target.value)} />
        </label>
      </div>
    </Modal>
  );
}

function MonitorGroup({
  kind,
  checks,
  skipped,
}: {
  kind: (typeof MONITOR_KINDS)[number];
  checks: Check[];
  skipped: MonitorPackSkipped[];
}) {
  return (
    <div className="card monitor-group">
      <div className="card-pad monitor-group-head">
        <div>
          <h3>{kind.label}</h3>
          <p>{kind.hint}</p>
        </div>
        <span className="badge">{checks.length}</span>
      </div>
      {skipped.length > 0 && (
        <div className="info-box" style={{ margin: "0 12px 10px" }}>
          {skipped.map((row) => (
            <div key={`${row.kind}-${row.column_name ?? ""}-${row.code}`}>
              {row.column_name ? `${row.column_name}: ` : ""}
              {row.reason}
            </div>
          ))}
        </div>
      )}
      {checks.length === 0 ? (
        <div className="empty compact">No managed monitors for this kind yet.</div>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Monitor</th>
                <th>Severity</th>
                <th>Status</th>
                <th>Last result</th>
                <th>Next run</th>
                <th>Schedule</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((check) => (
                <tr key={check.id}>
                  <td>
                    <div style={{ fontWeight: 700, color: "var(--text-dark)" }}>
                      <Link to={`/checks/${check.id}`} className="row-title-link">
                        {check.name}
                      </Link>
                    </div>
                    <div style={{ fontSize: 11.5, color: "var(--text-light)" }}>
                      {checkTypeLabel(check.check_type)}
                      {check.column_name ? ` - ${check.column_name}` : ""}
                    </div>
                  </td>
                  <td>
                    <SeverityBadge severity={check.severity} />
                  </td>
                  <td>
                    <StatusPill value={check.status} />
                  </td>
                  <td>
                    {check.last_status ? (
                      <>
                        <StatusPill value={check.last_status} />{" "}
                        <span style={{ fontSize: 11.5, color: "var(--text-light)" }}>{timeAgo(check.last_run_at)}</span>
                      </>
                    ) : (
                      <span style={{ color: "var(--text-light)" }}>never ran</span>
                    )}
                  </td>
                  <td style={{ color: "var(--text-light)", whiteSpace: "nowrap" }}>{fmtDateTime(check.next_run_at)}</td>
                  <td style={{ color: "var(--text-light)", whiteSpace: "nowrap" }}>
                    {describeSchedule(check.schedule_kind, check.schedule_expr)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function MonitorPackTab({
  datasetId,
  hasProfile,
  onProfileNow,
  profiling,
}: {
  datasetId: number;
  hasProfile: boolean;
  onProfileNow?: () => void;
  profiling?: boolean;
}) {
  const { user } = useAuth();
  const editable = canEdit(user);
  const qc = useQueryClient();
  const [configOpen, setConfigOpen] = useState(false);

  const packQuery = useQuery({
    queryKey: qk.monitorPack.detail(datasetId),
    queryFn: () => api.get<MonitorPackState>(`/datasets/${datasetId}/monitor-pack`),
    retry: false,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: qk.monitorPack.detail(datasetId) });
    qc.invalidateQueries({ queryKey: qk.checks.all });
    qc.invalidateQueries({ queryKey: qk.datasets.all });
  };

  const reconcile = useMutation({
    mutationFn: () => api.post<MonitorPackState>(`/datasets/${datasetId}/monitor-pack/reconcile`),
    onSuccess: invalidate,
  });

  const updatePack = useMutation({
    mutationFn: (body: { enabled?: boolean; config?: MonitorPackConfig }) =>
      api.patch<MonitorPackState>(`/datasets/${datasetId}/monitor-pack`, body),
    onSuccess: () => {
      setConfigOpen(false);
      invalidate();
    },
  });

  const pack = packQuery.data;
  const checks = pack?.managed_checks ?? [];
  const skipped = pack?.reconciliation?.skipped ?? [];
  const needsProfile = !hasProfile || pack?.status === "pending_profile" || pack?.reconciliation?.status === "pending_profile";
  const config = mergeConfig(pack?.config);

  if (packQuery.isLoading) return <Spinner label="Loading monitor pack..." />;

  return (
    <div className="monitor-pack-tab">
      <ErrorBox error={packQuery.error || reconcile.error || updatePack.error} />

      {needsProfile && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <EmptyState
            title="Profile this dataset before reconciling monitors"
            hint="Monitor packs need column stats and table facts before freshness, volume, schema, and drift checks can be managed."
          >
            {editable && onProfileNow && (
              <button className="primary" onClick={onProfileNow} disabled={profiling}>
                {profiling ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="refresh" size={14} />}
                {profiling ? "Profiling..." : "Profile now"}
              </button>
            )}
          </EmptyState>
        </div>
      )}

      {pack && (
        <>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <div className="card stat-card">
              <div className="label">Pack state</div>
              <div className="value" style={{ fontSize: 18 }}>
                <StatusPill value={packStatus(pack)} />
              </div>
              <div className="hint">{pack.reconciliation?.message || pack.last_error || "Managed monitors stay aligned with this dataset."}</div>
            </div>
            <div className="card stat-card">
              <div className="label">Last reconciled</div>
              <div className="value" style={{ fontSize: 18 }}>{fmtDateTime(pack.last_reconciled_at)}</div>
              <div className="hint">{timeAgo(pack.last_reconciled_at)}</div>
            </div>
            <div className="card stat-card">
              <div className="label">Managed checks</div>
              <div className="value">{checks.length}</div>
              <div className="hint">{MONITOR_KINDS.filter((k) => config.monitors[k.key]).length} kinds enabled</div>
            </div>
            <div className="card stat-card">
              <div className="label">Cadence</div>
              <div className="value" style={{ fontSize: 15, lineHeight: 1.35 }}>{cadenceLabel(config)}</div>
              <div className="hint">Per-kind interval schedule</div>
            </div>
          </div>

          <div className="toolbar">
            {editable ? (
              <>
                <button className="primary" onClick={() => reconcile.mutate()} disabled={reconcile.isPending || needsProfile}>
                  {reconcile.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="refresh" size={14} />}
                  {reconcile.isPending ? "Reconciling..." : "Reconcile now"}
                </button>
                <button onClick={() => updatePack.mutate({ enabled: !pack.enabled })} disabled={updatePack.isPending}>
                  {pack.enabled ? "Disable pack" : "Enable pack"}
                </button>
                <button onClick={() => setConfigOpen(true)}>Configure</button>
              </>
            ) : (
              <span className="badge">viewer: read only</span>
            )}
            <div className="right">
              <Link to={`/datasets/${datasetId}/checks`} className="btn">
                Open checks tab
              </Link>
            </div>
          </div>

          {pack.last_error && <div className="error-box">{pack.last_error}</div>}

          {checks.length === 0 && !needsProfile && (
            <div className="card" style={{ marginBottom: 16 }}>
              <EmptyState title="No managed checks yet" hint="Reconcile the pack to create or refresh dataset-level monitors.">
                {editable && (
                  <button className="primary" onClick={() => reconcile.mutate()} disabled={reconcile.isPending}>
                    <Icon name="refresh" size={14} /> Reconcile now
                  </button>
                )}
              </EmptyState>
            </div>
          )}

          <div className="monitor-group-grid">
            {MONITOR_KINDS.map((kind) => (
              <MonitorGroup
                key={kind.key}
                kind={kind}
                checks={checks.filter((check) => monitorKind(check) === kind.key)}
                skipped={skipped.filter((row) => row.kind === kind.key)}
              />
            ))}
          </div>
        </>
      )}

      {!pack && !packQuery.error && (
        <div className="card">
          <EmptyState title="Monitor pack unavailable" hint="The backend did not return pack state for this dataset." />
        </div>
      )}

      {pack && configOpen && (
        <MonitorConfigModal
          pack={pack}
          saving={updatePack.isPending}
          error={updatePack.error}
          onSave={(nextConfig) => updatePack.mutate({ config: nextConfig })}
          onClose={() => setConfigOpen(false)}
        />
      )}
    </div>
  );
}
