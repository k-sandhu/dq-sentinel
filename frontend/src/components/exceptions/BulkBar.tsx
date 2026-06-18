// Sticky bulk-action bar shown inside the table card when selection >= 1 (#63).
// Same mutation path as the panel/keyboard; selection survives paging.

import { useState } from "react";
import type { Assignee } from "../../api/types";
import { SELECTION_CAP, TRIAGE_ACTIONS } from "./shared";

export default function BulkBar({
  count,
  assignees,
  onTriage,
  onClear,
  triaging,
}: {
  count: number;
  assignees: Assignee[];
  onTriage: (payload: {
    status?: string;
    note?: string;
    assigned_to_id?: number | null;
    clear_assignee?: boolean;
  }) => void;
  onClear: () => void;
  triaging: boolean;
}) {
  const [note, setNote] = useState("");

  function act(status: string) {
    onTriage({ status, note: note || undefined });
    setNote("");
  }

  return (
    <div className="xw-bulkbar" role="region" aria-label="Bulk triage actions">
      <strong>{count} selected</strong>
      {count >= SELECTION_CAP && <span className="xw-cap-note">selection limited to {SELECTION_CAP}</span>}
      <div className="xw-bulk-actions">
        {TRIAGE_ACTIONS.map((a) => (
          <button key={a.status} className="small" title={`${a.hint} (${a.key})`} disabled={triaging} onClick={() => act(a.status)}>
            {a.label}
          </button>
        ))}
      </div>
      <select
        aria-label="Assign selected"
        defaultValue=""
        disabled={triaging}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "__none") onTriage({ clear_assignee: true });
          else if (v) onTriage({ assigned_to_id: Number(v) });
          e.currentTarget.value = "";
        }}
      >
        <option value="">Assign…</option>
        <option value="__none">Unassign</option>
        {assignees.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name || a.email}
          </option>
        ))}
      </select>
      <input
        type="text"
        aria-label="Bulk triage note"
        placeholder="Optional note"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        className="xw-note-input"
      />
      <button className="small ghost" onClick={onClear}>
        Clear
      </button>
    </div>
  );
}
