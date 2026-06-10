import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import type { ExceptionRecord, ExceptionStatus } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { fmtDateTime, fmtValue } from "../lib/format";
import { EmptyState, ErrorBox, Modal, Pill, Spinner } from "./ui";

const STATUSES: ExceptionStatus[] = ["open", "acknowledged", "expected", "resolved", "muted"];

const TRIAGE_ACTIONS: { status: ExceptionStatus; label: string; hint: string }[] = [
  { status: "acknowledged", label: "Acknowledge", hint: "Seen, investigation pending" },
  { status: "expected", label: "Mark expected", hint: "Legitimate data — reference for future" },
  { status: "resolved", label: "Resolve", hint: "Underlying data fixed" },
  { status: "muted", label: "Mute", hint: "Stop counting this one" },
  { status: "open", label: "Reopen", hint: "Back to the queue" },
];

function RowDetailModal({ exc, onClose }: { exc: ExceptionRecord; onClose: () => void }) {
  return (
    <Modal title={`Exception #${exc.id}`} onClose={onClose} wide>
      <div style={{ marginBottom: 10, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <Pill value={exc.status} /> <strong>{exc.reason}</strong>
        {exc.outlier_score != null && <span className="score-chip">score {exc.outlier_score}</span>}
        <Link
          to={`/workbench?dataset_id=${exc.dataset_id}&exception_id=${exc.id}`}
          className="btn small"
          style={{ marginLeft: "auto" }}
        >
          Investigate in workbench →
        </Link>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--text-light)", marginBottom: 12 }}>
        {exc.check_name} · {exc.dataset_name} · run #{exc.run_id} · {fmtDateTime(exc.created_at)}
        {exc.note && (
          <div style={{ marginTop: 4 }}>
            Note: <em>{exc.note}</em> {exc.marked_by ? `— ${exc.marked_by}` : ""}
          </div>
        )}
      </div>
      <div className="table-wrap card" style={{ boxShadow: "none" }}>
        <table className="data">
          <thead>
            <tr>
              <th>Column</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(exc.row_data).map(([k, v]) => (
              <tr key={k}>
                <td style={{ fontWeight: 600, width: 180 }}>{k}</td>
                <td className="mono" style={{ color: exc.column_name === k ? "var(--danger-dark)" : undefined, fontWeight: exc.column_name === k ? 700 : 400 }}>
                  {fmtValue(v)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}

export default function ExceptionsTriage({
  datasetId,
  runId,
  checkId,
}: {
  datasetId?: number;
  runId?: number;
  checkId?: number;
}) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const editable = canEdit(user);
  const [statusFilter, setStatusFilter] = useState<string>("open");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [note, setNote] = useState("");
  const [detail, setDetail] = useState<ExceptionRecord | null>(null);

  const params = new URLSearchParams();
  if (datasetId) params.set("dataset_id", String(datasetId));
  if (runId) params.set("run_id", String(runId));
  if (checkId) params.set("check_id", String(checkId));
  if (statusFilter !== "all") params.set("status", statusFilter);
  params.set("limit", "200");
  const qs = params.toString();

  const { data, isLoading, error } = useQuery({
    queryKey: ["exceptions", qs],
    queryFn: () => api.get<ExceptionRecord[]>(`/exceptions?${qs}`),
  });

  const triage = useMutation({
    mutationFn: (status: ExceptionStatus) =>
      api.post<ExceptionRecord[]>("/exceptions/triage", { ids: [...selected], status, note }),
    onSuccess: () => {
      setSelected(new Set());
      setNote("");
      qc.invalidateQueries({ queryKey: ["exceptions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
  });

  const excs = useMemo(() => data ?? [], [data]);
  const allSelected = excs.length > 0 && excs.every((e) => selected.has(e.id));

  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(excs.map((e) => e.id)));
  const toggle = (id: number) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  return (
    <div>
      <div className="toolbar">
        <div className="chip-row">
          {["all", ...STATUSES].map((s) => (
            <button
              key={s}
              className={`filter-chip${statusFilter === s ? " on" : ""}`}
              onClick={() => {
                setStatusFilter(s);
                setSelected(new Set());
              }}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {editable && selected.size > 0 && (
        <div className="card card-pad" style={{ marginBottom: 14, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <strong>{selected.size} selected</strong>
          <input
            type="text"
            placeholder="Optional note (why is this expected / what was fixed)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            style={{ flex: 1, minWidth: 220, marginTop: 0 }}
          />
          {TRIAGE_ACTIONS.map((a) => (
            <button key={a.status} className="small" title={a.hint} onClick={() => triage.mutate(a.status)} disabled={triage.isPending}>
              {a.label}
            </button>
          ))}
        </div>
      )}
      <ErrorBox error={error || triage.error} />

      {isLoading ? (
        <Spinner />
      ) : !excs.length ? (
        <EmptyState
          title={statusFilter === "open" ? "No open exceptions" : "Nothing here"}
          hint={statusFilter === "open" ? "All clear — failed runs will surface violating rows here." : undefined}
        />
      ) : (
        <div className="card table-wrap">
          <table className="data">
            <thead>
              <tr>
                {editable && (
                  <th style={{ width: 30 }}>
                    <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                  </th>
                )}
                <th>Status</th>
                <th>Reason</th>
                <th>Row data</th>
                <th className="num">Score</th>
                <th>Check</th>
                {!datasetId && <th>Dataset</th>}
                <th>Seen</th>
              </tr>
            </thead>
            <tbody>
              {excs.map((e) => (
                <tr key={e.id} className="clickable" onClick={() => setDetail(e)}>
                  {editable && (
                    <td onClick={(ev) => ev.stopPropagation()}>
                      <input type="checkbox" checked={selected.has(e.id)} onChange={() => toggle(e.id)} />
                    </td>
                  )}
                  <td>
                    <Pill value={e.status} />
                  </td>
                  <td style={{ maxWidth: 280, fontWeight: 600, color: "var(--text-dark)" }}>
                    {e.reason}
                    {e.note && <div style={{ fontWeight: 400, fontSize: 11.5, color: "var(--text-light)" }}>{e.note}</div>}
                  </td>
                  <td>
                    <div className="rowdata">
                      {Object.entries(e.row_data)
                        .slice(0, 5)
                        .map(([k, v]) => `${k}=${fmtValue(v)}`)
                        .join("  ")}
                    </div>
                  </td>
                  <td className="num">{e.outlier_score != null ? <span className="score-chip">{e.outlier_score}</span> : ""}</td>
                  <td style={{ fontSize: 12 }}>{e.check_name}</td>
                  {!datasetId && <td style={{ fontSize: 12 }}>{e.dataset_name}</td>}
                  <td style={{ whiteSpace: "nowrap", fontSize: 12, color: "var(--text-light)" }}>{fmtDateTime(e.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {detail && <RowDetailModal exc={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}
