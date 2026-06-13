import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import type { Check, CheckTypeInfo, Run } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { checkTypeLabel, originLabel } from "../lib/checkMeta";
import { describeSchedule, timeAgo } from "../lib/format";
import CheckParamsForm from "./CheckParamsForm";
import { EmptyState, ErrorBox, Icon, Modal, Pill, SeverityDot } from "./ui";

function paramsSummary(check: Check): string {
  const entries = Object.entries(check.params ?? {});
  if (!entries.length) return "";
  return entries
    .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
    .join(", ");
}

function EditCheckModal({ check, onClose }: { check: Check; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(check.name);
  const [severity, setSeverity] = useState(check.severity);
  const [scheduleKind, setScheduleKind] = useState(check.schedule_kind ?? "interval");
  const [scheduleExpr, setScheduleExpr] = useState(check.schedule_expr ?? "1440");
  const [params, setParams] = useState<Record<string, unknown>>(check.params ?? {});
  const [paramsError, setParamsError] = useState<string | null>(null);

  const { data: types, error: typesError } = useQuery({
    queryKey: ["check-types"],
    queryFn: () => api.get<CheckTypeInfo[]>("/checks/types"),
  });
  const selected = types?.find((type) => type.key === check.check_type);
  const onParamsChange = useCallback((next: Record<string, unknown>, error: string | null) => {
    setParams(next);
    setParamsError(error);
  }, []);

  const save = useMutation({
    mutationFn: () =>
      api.patch<Check>(`/checks/${check.id}`, {
        name,
        severity,
        schedule_kind: scheduleKind,
        schedule_expr: scheduleExpr,
        params,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["checks"] });
      onClose();
    },
  });

  return (
    <Modal
      title="Edit check"
      onClose={onClose}
      wide
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={() => save.mutate()} disabled={save.isPending || Boolean(paramsError)}>
            Save
          </button>
        </>
      }
    >
      <ErrorBox error={save.error || typesError} />
      <label className="field">
        Name
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <div className="form-row">
        <label className="field">
          Severity
          <select value={severity} onChange={(e) => setSeverity(e.target.value as Check["severity"])}>
            <option value="info">info</option>
            <option value="warn">warn</option>
            <option value="error">error</option>
          </select>
        </label>
        <label className="field">
          Schedule
          <div style={{ display: "flex", gap: 6 }}>
            <select value={scheduleKind} onChange={(e) => setScheduleKind(e.target.value)} style={{ width: 110 }}>
              <option value="interval">interval</option>
              <option value="cron">cron</option>
            </select>
            <input
              type="text"
              value={scheduleExpr}
              onChange={(e) => setScheduleExpr(e.target.value)}
              placeholder={scheduleKind === "interval" ? "minutes, e.g. 1440" : "0 6 * * *"}
            />
          </div>
        </label>
      </div>
      {selected ? (
        <CheckParamsForm typeInfo={selected} params={check.params ?? {}} onChange={onParamsChange} />
      ) : (
        <div className="info-box">Loading parameter schema...</div>
      )}
    </Modal>
  );
}

export default function ChecksTable({
  checks,
  showDataset = true,
  onRunFinished,
}: {
  checks: Check[];
  showDataset?: boolean;
  onRunFinished?: (run: Run) => void;
}) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const editable = canEdit(user);
  const [editing, setEditing] = useState<Check | null>(null);
  const [runningId, setRunningId] = useState<number | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["checks"] });
    qc.invalidateQueries({ queryKey: ["runs"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api.patch<Check>(`/checks/${id}`, { status }),
    onSuccess: invalidate,
  });

  const archive = useMutation({
    mutationFn: (id: number) => api.del(`/checks/${id}`),
    onSuccess: invalidate,
  });

  const runNow = useMutation({
    mutationFn: (id: number) => api.post<Run>(`/checks/${id}/run`),
    onMutate: (id) => setRunningId(id),
    onSettled: () => setRunningId(null),
    onSuccess: (run) => {
      invalidate();
      onRunFinished?.(run);
    },
  });

  const proposed = checks.filter((check) => check.status === "proposed");
  const rest = checks.filter((check) => check.status !== "proposed");

  if (!checks.length) {
    return (
      <EmptyState
        title="No checks yet"
        hint="Profile the dataset, then generate checks - or add one manually."
      />
    );
  }

  return (
    <>
      <ErrorBox error={setStatus.error || runNow.error || archive.error} />
      {proposed.length > 0 && (
        <div className="card" style={{ marginBottom: 16, borderColor: "#e0d2ef" }}>
          <div className="card-pad" style={{ paddingBottom: 8 }}>
            <h3>
              <span style={{ color: "var(--purple)" }}>●</span> Proposed checks ({proposed.length}) - review and activate
            </h3>
          </div>
          <div className="table-wrap">
            <table className="data">
              <tbody>
                {proposed.map((check) => (
                  <tr key={check.id}>
                    <td style={{ width: 24 }}>
                      <span className={`badge ${check.origin === "llm" ? "ai" : ""}`}>{originLabel(check.origin)}</span>
                    </td>
                    <td>
                      <div style={{ fontWeight: 700, color: "var(--text-dark)" }}>
                        <Link to={`/checks/${check.id}`}>{checkTypeLabel(check.check_type)}</Link>
                        {check.column_name && <span style={{ fontWeight: 400 }}> on </span>}
                        {check.column_name && <code>{check.column_name}</code>}
                        {showDataset && (
                          <span style={{ fontWeight: 400, color: "var(--text-light)" }}> · {check.dataset_name}</span>
                        )}
                      </div>
                      <div style={{ fontSize: 12, color: "var(--text-light)", marginTop: 2 }}>{check.rationale}</div>
                      {paramsSummary(check) && <div className="rowdata" style={{ marginTop: 2 }}>{paramsSummary(check)}</div>}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <SeverityDot severity={check.severity} />
                    </td>
                    <td style={{ whiteSpace: "nowrap", color: "var(--text-light)", fontSize: 12 }}>
                      {describeSchedule(check.schedule_kind, check.schedule_expr)}
                    </td>
                    {editable && (
                      <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                        <button className="primary small" onClick={() => setStatus.mutate({ id: check.id, status: "active" })}>
                          <Icon name="check" size={13} /> Activate
                        </button>{" "}
                        <button className="small" onClick={() => setEditing(check)}>
                          Edit
                        </button>{" "}
                        <button className="small danger" onClick={() => archive.mutate(check.id)}>
                          Dismiss
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {rest.length > 0 && (
        <div className="card">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Check</th>
                  {showDataset && <th>Dataset</th>}
                  <th>Severity</th>
                  <th>Schedule</th>
                  <th>Status</th>
                  <th>Last result</th>
                  {editable && <th style={{ textAlign: "right" }}>Actions</th>}
                </tr>
              </thead>
              <tbody>
                {rest.map((check) => (
                  <tr key={check.id}>
                    <td>
                      <Link to={`/checks/${check.id}`} style={{ fontWeight: 600 }}>
                        {check.name}
                      </Link>
                      <div style={{ fontSize: 11.5, color: "var(--text-light)" }}>
                        {checkTypeLabel(check.check_type)}
                        {check.column_name ? ` · ${check.column_name}` : ""} ·{" "}
                        <span className={`badge ${check.origin === "llm" ? "ai" : ""}`} style={{ fontSize: 10 }}>
                          {originLabel(check.origin)}
                        </span>
                      </div>
                    </td>
                    {showDataset && (
                      <td>
                        <Link to={`/datasets/${check.dataset_id}/checks`}>{check.dataset_name}</Link>
                      </td>
                    )}
                    <td>
                      <SeverityDot severity={check.severity} />
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-light)" }}>
                      {describeSchedule(check.schedule_kind, check.schedule_expr)}
                    </td>
                    <td>
                      <Pill value={check.status} />
                    </td>
                    <td>
                      {check.last_status ? (
                        <>
                          <Pill value={check.last_status} />{" "}
                          <span style={{ fontSize: 11.5, color: "var(--text-light)" }}>{timeAgo(check.last_run_at)}</span>
                        </>
                      ) : (
                        <span style={{ color: "var(--text-light)" }}>never ran</span>
                      )}
                    </td>
                    {editable && (
                      <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                        <button
                          className="small"
                          disabled={runningId === check.id}
                          onClick={() => runNow.mutate(check.id)}
                          title="Run now"
                        >
                          {runningId === check.id ? <span className="spinner" style={{ width: 12, height: 12 }} /> : <Icon name="play" size={12} />}
                          Run
                        </button>{" "}
                        {check.status === "active" ? (
                          <button className="small" onClick={() => setStatus.mutate({ id: check.id, status: "disabled" })}>
                            Pause
                          </button>
                        ) : (
                          <button className="small" onClick={() => setStatus.mutate({ id: check.id, status: "active" })}>
                            Resume
                          </button>
                        )}{" "}
                        <button className="small" onClick={() => setEditing(check)}>
                          Edit
                        </button>{" "}
                        <button className="small danger" onClick={() => archive.mutate(check.id)}>
                          Archive
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {editing && <EditCheckModal check={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
