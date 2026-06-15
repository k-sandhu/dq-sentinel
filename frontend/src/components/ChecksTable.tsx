import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type { Check, CheckTypeInfo, Run } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { checkTypeLabel, originLabel } from "../lib/checkMeta";
import { describeSchedule, timeAgo } from "../lib/format";
import { useConfirm } from "./confirm";
import CheckParamsForm, { validateParams } from "./CheckParamsForm";
import { EmptyState, ErrorBox, Icon, Modal, SeverityBadge, StatusPill } from "./ui";

function paramsSummary(c: Check): string {
  const entries = Object.entries(c.params ?? {});
  if (!entries.length) return "";
  return entries
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(", ");
}

function EditCheckModal({ check, onClose }: { check: Check; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: types } = useQuery({
    queryKey: ["check-types"],
    queryFn: () => api.get<CheckTypeInfo[]>("/checks/types"),
  });
  const [name, setName] = useState(check.name);
  const [severity, setSeverity] = useState(check.severity);
  const [scheduleKind, setScheduleKind] = useState(check.schedule_kind ?? "interval");
  const [scheduleExpr, setScheduleExpr] = useState(check.schedule_expr ?? "1440");
  const [params, setParams] = useState<Record<string, unknown>>(check.params ?? {});

  const selected = types?.find((t) => t.key === check.check_type);
  const paramErrors = validateParams(selected?.params ?? [], params);
  const hasParamErrors = Object.keys(paramErrors).length > 0;

  const dirty =
    name !== check.name ||
    severity !== check.severity ||
    scheduleKind !== (check.schedule_kind ?? "interval") ||
    scheduleExpr !== (check.schedule_expr ?? "1440") ||
    JSON.stringify(params) !== JSON.stringify(check.params ?? {});

  const save = useMutation({
    mutationFn: async () =>
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
      dirty={dirty}
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            onClick={() => save.mutate()}
            disabled={save.isPending || hasParamErrors}
          >
            Save
          </button>
        </>
      }
    >
      <ErrorBox error={save.error} />
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
      <div className="field-group-label">
        Parameters
        <span className="field-hint" style={{ fontWeight: 400, marginLeft: 6 }}>
          {checkTypeLabel(check.check_type)}
          {check.column_name ? ` · ${check.column_name}` : ""}
        </span>
      </div>
      <CheckParamsForm specs={selected?.params} params={params} onChange={setParams} errors={paramErrors} />
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
  const confirm = useConfirm();
  const navigate = useNavigate();
  const editable = canEdit(user);
  const [editing, setEditing] = useState<Check | null>(null);
  const [runningId, setRunningId] = useState<number | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["checks"] });
    qc.invalidateQueries({ queryKey: ["runs"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      api.patch<Check>(`/checks/${id}`, { status }),
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

  const proposed = checks.filter((c) => c.status === "proposed");
  const rest = checks.filter((c) => c.status !== "proposed");

  if (!checks.length) {
    return (
      <EmptyState
        title="No checks yet"
        hint="Profile the dataset, then generate checks — or add one manually."
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
              <span style={{ color: "var(--purple)" }}>●</span> Proposed checks ({proposed.length}) — review
              and activate
            </h3>
          </div>
          <div className="table-wrap">
            <table className="data">
              <tbody>
                {proposed.map((c) => (
                  <tr key={c.id} className="clickable" onClick={() => navigate(`/checks/${c.id}`)}>
                    <td style={{ width: 24 }}>
                      <span className={`badge ${c.origin === "llm" ? "ai" : ""}`}>{originLabel(c.origin)}</span>
                    </td>
                    <td>
                      <div style={{ fontWeight: 700, color: "var(--text-dark)" }}>
                        <Link to={`/checks/${c.id}`} className="row-title-link" onClick={(e) => e.stopPropagation()}>
                          {checkTypeLabel(c.check_type)}
                        </Link>
                        {c.column_name && <span style={{ fontWeight: 400 }}> on </span>}
                        {c.column_name && <code>{c.column_name}</code>}
                        {showDataset && (
                          <span style={{ fontWeight: 400, color: "var(--text-light)" }}> · {c.dataset_name}</span>
                        )}
                      </div>
                      <div style={{ fontSize: 12, color: "var(--text-light)", marginTop: 2 }}>{c.rationale}</div>
                      {paramsSummary(c) && (
                        <div className="rowdata" style={{ marginTop: 2 }}>{paramsSummary(c)}</div>
                      )}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <SeverityBadge severity={c.severity} />
                    </td>
                    <td style={{ whiteSpace: "nowrap", color: "var(--text-light)", fontSize: 12 }}>
                      {describeSchedule(c.schedule_kind, c.schedule_expr)}
                    </td>
                    {editable && (
                      <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                        <button
                          className="primary small"
                          onClick={(e) => {
                            e.stopPropagation();
                            setStatus.mutate({ id: c.id, status: "active" });
                          }}
                        >
                          <Icon name="check" size={13} /> Activate
                        </button>{" "}
                        <button
                          className="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditing(c);
                          }}
                        >
                          Edit
                        </button>{" "}
                        <button
                          className="small danger"
                          onClick={async (e) => {
                            e.stopPropagation();
                            if (
                              await confirm({
                                title: "Dismiss proposed check",
                                danger: true,
                                confirmLabel: "Dismiss",
                                body: (
                                  <>
                                    Dismiss the proposed <strong>{checkTypeLabel(c.check_type)}</strong>
                                    {c.column_name ? (
                                      <>
                                        {" "}
                                        on <code>{c.column_name}</code>
                                      </>
                                    ) : null}{" "}
                                    check? It will be removed from proposals.
                                  </>
                                ),
                              })
                            )
                              archive.mutate(c.id);
                          }}
                        >
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
                {rest.map((c) => (
                  <tr key={c.id} className="clickable" onClick={() => navigate(`/checks/${c.id}`)}>
                    <td>
                      <div style={{ fontWeight: 600 }}>
                        <Link to={`/checks/${c.id}`} className="row-title-link" onClick={(e) => e.stopPropagation()}>
                          {c.name}
                        </Link>
                      </div>
                      <div style={{ fontSize: 11.5, color: "var(--text-light)" }}>
                        {checkTypeLabel(c.check_type)}
                        {c.column_name ? ` · ${c.column_name}` : ""} ·{" "}
                        <span className={`badge ${c.origin === "llm" ? "ai" : ""}`} style={{ fontSize: 10 }}>
                          {originLabel(c.origin)}
                        </span>
                      </div>
                    </td>
                    {showDataset && (
                      <td>
                        <Link to={`/datasets/${c.dataset_id}/checks`} onClick={(e) => e.stopPropagation()}>
                          {c.dataset_name}
                        </Link>
                      </td>
                    )}
                    <td>
                      <SeverityBadge severity={c.severity} />
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-light)" }}>
                      {describeSchedule(c.schedule_kind, c.schedule_expr)}
                    </td>
                    <td>
                      <StatusPill value={c.status} />
                    </td>
                    <td>
                      {c.last_status ? (
                        <>
                          <StatusPill value={c.last_status} />{" "}
                          <span style={{ fontSize: 11.5, color: "var(--text-light)" }}>{timeAgo(c.last_run_at)}</span>
                        </>
                      ) : (
                        <span style={{ color: "var(--text-light)" }}>never ran</span>
                      )}
                    </td>
                    {editable && (
                      <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                        <button
                          className="small"
                          disabled={runningId === c.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            runNow.mutate(c.id);
                          }}
                          title="Run now"
                        >
                          {runningId === c.id ? <span className="spinner" style={{ width: 12, height: 12 }} /> : <Icon name="play" size={12} />}
                          Run
                        </button>{" "}
                        {c.status === "active" ? (
                          <button
                            className="small"
                            onClick={(e) => {
                              e.stopPropagation();
                              setStatus.mutate({ id: c.id, status: "disabled" });
                            }}
                          >
                            Pause
                          </button>
                        ) : (
                          <button
                            className="small"
                            onClick={(e) => {
                              e.stopPropagation();
                              setStatus.mutate({ id: c.id, status: "active" });
                            }}
                          >
                            Resume
                          </button>
                        )}{" "}
                        <button
                          className="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditing(c);
                          }}
                        >
                          Edit
                        </button>{" "}
                        <button
                          className="small danger"
                          onClick={async (e) => {
                            e.stopPropagation();
                            if (
                              await confirm({
                                title: "Archive check",
                                danger: true,
                                confirmLabel: "Archive",
                                body: (
                                  <>
                                    Archive <strong>{c.name}</strong>? It stops running and leaves the
                                    active list.
                                  </>
                                ),
                              })
                            )
                              archive.mutate(c.id);
                          }}
                        >
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
