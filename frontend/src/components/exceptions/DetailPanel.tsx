// Right-hand drawer: row, check context, activity timeline, comments, actions.
// Replaces the old modal so the analyst keeps their place in the list (#63).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Assignee, Check, ExceptionEvent, ExceptionRecord } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import { checkTypeLabel } from "../../lib/checkMeta";
import { fmtDateTime, fmtRelative, fmtValue } from "../../lib/format";
import { Icon, Spinner } from "../ui";
import { SevBadge, StatusPill, TRIAGE_ACTIONS } from "./shared";

function paramSummary(check: Check | undefined): string {
  if (!check || !check.params) return "";
  const entries = Object.entries(check.params).filter(([, v]) => v !== null && v !== "");
  if (!entries.length) return "";
  return entries.map(([k, v]) => `${k}=${fmtValue(v)}`).join(", ");
}

export default function DetailPanel({
  exc,
  assignees,
  onClose,
  onTriage,
  triaging,
  returnFocusRef,
}: {
  exc: ExceptionRecord;
  assignees: Assignee[];
  onClose: () => void;
  onTriage: (
    ids: number[],
    payload: { status?: string; note?: string; assigned_to_id?: number | null; clear_assignee?: boolean },
  ) => void;
  triaging: boolean;
  returnFocusRef: MutableRefObject<HTMLElement | null>;
}) {
  const { user } = useAuth();
  const editable = canEdit(user);
  const qc = useQueryClient();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [note, setNote] = useState("");
  const [comment, setComment] = useState("");

  // Move focus to the panel heading on open; return focus to the originating
  // row on close/unmount (the #1 keyboard-user complaint if lost).
  useEffect(() => {
    headingRef.current?.focus();
    return () => {
      returnFocusRef.current?.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exc.id]);

  // No single-check GET endpoint exists; fetch the dataset's checks (cached and
  // shared with the Checks tab) and pick this one for params/rationale context.
  const { data: checks } = useQuery({
    queryKey: qk.checks.byDatasetId(exc.dataset_id),
    queryFn: () => api.get<Check[]>(`/checks?dataset_id=${exc.dataset_id}`),
  });
  const check = checks?.find((c) => c.id === exc.check_id);

  const { data: events, isLoading: eventsLoading } = useQuery({
    queryKey: qk.exceptionEvents.detail(exc.id),
    queryFn: () => api.get<ExceptionEvent[]>(`/exceptions/${exc.id}/events`),
  });

  const addComment = useMutation({
    mutationFn: () => api.post<ExceptionEvent>(`/exceptions/${exc.id}/comments`, { comment }),
    onSuccess: () => {
      setComment("");
      qc.invalidateQueries({ queryKey: qk.exceptionEvents.detail(exc.id) });
      qc.invalidateQueries({ queryKey: qk.exceptions.all });
    },
  });

  function act(status: string) {
    onTriage([exc.id], { status, note: note || undefined });
    setNote("");
  }

  const lastRun = exc.last_run_id ?? exc.run_id;

  return (
    <aside className="xw-panel" role="complementary" aria-label="Exception detail">
      <div className="xw-panel-head">
        <h3 tabIndex={-1} ref={headingRef} className="xw-panel-title">
          Exception #{exc.id}
        </h3>
        <button className="ghost small icon-only" onClick={onClose} aria-label="Close detail panel" title="Close (Esc)">
          <Icon name="x" />
        </button>
      </div>

      <div className="xw-panel-body">
        {/* 1. Header: severity + status + recurrence + seen + assignee */}
        <div className="xw-panel-summary">
          <div className="xw-panel-badges">
            <SevBadge severity={exc.check_severity} />
            <StatusPill status={exc.status} />
            {exc.occurrence_count > 1 && (
              <span className="xw-occ" title={`Seen ${exc.occurrence_count} times`}>
                ×{exc.occurrence_count}
              </span>
            )}
          </div>
          <div className="xw-panel-reason">{exc.reason}</div>
          <div className="xw-panel-meta">
            First seen {fmtRelative(exc.first_seen_at)} · last seen {fmtRelative(exc.last_seen_at)}
            {exc.assigned_to ? ` · assigned to ${exc.assigned_to}` : " · unassigned"}
          </div>
        </div>

        {/* 2. Triage actions */}
        {editable && (
          <div className="xw-panel-section">
            <div className="xw-actions">
              {TRIAGE_ACTIONS.map((a) => (
                <button
                  key={a.status}
                  className="small"
                  title={`${a.hint} (${a.key})`}
                  disabled={triaging}
                  onClick={() => act(a.status)}
                >
                  {a.label}
                </button>
              ))}
            </div>
            <div className="xw-panel-row">
              <select
                aria-label="Assign exception"
                value={exc.assigned_to_id ?? ""}
                disabled={triaging}
                onChange={(e) => {
                  const v = e.target.value;
                  if (!v) onTriage([exc.id], { clear_assignee: true });
                  else onTriage([exc.id], { assigned_to_id: Number(v) });
                }}
              >
                <option value="">Unassigned</option>
                {assignees.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name || a.email}
                  </option>
                ))}
              </select>
            </div>
            <input
              type="text"
              aria-label="Triage note"
              placeholder="Optional note (applied with the next status change)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="xw-note-input"
            />
          </div>
        )}

        {/* 3. Row data with the failing column highlighted */}
        <div className="xw-panel-section">
          <div className="xw-panel-label">Row data</div>
          <div className="table-wrap">
            <table className="data xw-rowdata-table">
              <tbody>
                {Object.entries(exc.row_data).map(([k, v]) => {
                  const hit = check?.column_name === k || exc.column_name === k;
                  return (
                    <tr key={k}>
                      <td className="xw-rd-key">{k}</td>
                      <td className={`mono${hit ? " xw-rd-hit" : ""}`}>{fmtValue(v)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* 4. Check context card */}
        <div className="xw-panel-section">
          <div className="xw-panel-label">Check</div>
          <div className="xw-check-card">
            <Link to={`/datasets/${exc.dataset_id}/checks`} className="xw-check-name">
              {exc.check_name}
            </Link>
            <div className="xw-check-meta">
              {checkTypeLabel(exc.check_type)}
              {` · ${exc.check_severity}`}
              {paramSummary(check) ? ` · ${paramSummary(check)}` : ""}
            </div>
            {check?.rationale && <div className="xw-check-rationale">{check.rationale}</div>}
          </div>
        </div>

        {/* 5. Activity timeline + comment composer */}
        <div className="xw-panel-section">
          <div className="xw-panel-label">Activity</div>
          {eventsLoading ? (
            <Spinner />
          ) : !events || events.length === 0 ? (
            <div className="xw-muted">No activity yet.</div>
          ) : (
            <div className="timeline">
              {events.map((ev) => (
                <div key={ev.id} className="timeline-item">
                  <span className="timeline-dot" />
                  <div className="title">{eventTitle(ev)}</div>
                  <div className="meta">
                    {ev.user ?? "System"} · {fmtDateTime(ev.created_at)}
                  </div>
                  {ev.comment && ev.kind !== "assign" && (
                    <div className="xw-event-comment">{ev.comment}</div>
                  )}
                </div>
              ))}
            </div>
          )}
          {editable && (
            <div className="xw-comment-composer">
              <input
                type="text"
                aria-label="Add a comment"
                placeholder="Add a comment…"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && comment.trim()) addComment.mutate();
                }}
              />
              <button
                className="small primary"
                disabled={!comment.trim() || addComment.isPending}
                onClick={() => addComment.mutate()}
              >
                Comment
              </button>
            </div>
          )}
        </div>

        {/* 6. Links */}
        <div className="xw-panel-section xw-links">
          <Link to={`/runs/${lastRun}`} className="btn small">
            Run #{lastRun}
          </Link>
          <Link to={`/workbench?dataset_id=${exc.dataset_id}&exception_id=${exc.id}`} className="btn small">
            Investigate in workbench →
          </Link>
          <Link to={`/datasets/${exc.dataset_id}?tab=rca`} className="btn small">
            RCA
          </Link>
        </div>
      </div>
    </aside>
  );
}

function eventTitle(ev: ExceptionEvent): string {
  if (ev.kind === "status") return `${ev.from_status || "—"} → ${ev.to_status || "—"}`;
  if (ev.kind === "assign") return ev.comment || "assignment changed";
  if (ev.kind === "comment") return "comment";
  return ev.comment || "system";
}
