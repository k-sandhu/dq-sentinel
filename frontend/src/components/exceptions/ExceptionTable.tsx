// The triage table: columns, client-side grouping of the current page,
// selection (survives paging), server pagination, column-visibility menu, and
// keyboard-focus highlight with aria-selected rows (#63).

import { Fragment, useMemo, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import type { ExceptionRecord } from "../../api/types";
import { fmtRelative, isRecent } from "../../lib/format";
import { getPref, PREF_KEYS, setPref } from "../../lib/prefs";
import { Icon } from "../ui";
import { SevBadge, StatusPill } from "./shared";
import type { GroupMode } from "./shared";

// Column ids that can be toggled in the gear menu (severity/status/reason are
// always shown). `dataset` is auto-hidden when pinned via props.
const TOGGLE_COLS = [
  { id: "occurrences", label: "Occurrences" },
  { id: "last_seen", label: "Last seen" },
  { id: "assignee", label: "Assignee" },
  { id: "check", label: "Check" },
  { id: "dataset", label: "Dataset" },
] as const;

export default function ExceptionTable({
  items,
  total,
  offset,
  pageSize,
  loading,
  group,
  selected,
  focusedId,
  editable,
  hideDataset,
  rowRefs,
  onToggleSelect,
  onToggleGroup,
  onOpen,
  onPage,
}: {
  items: ExceptionRecord[];
  total: number;
  offset: number;
  pageSize: number;
  loading: boolean;
  group: GroupMode;
  selected: Set<number>;
  focusedId: number | null;
  editable: boolean;
  hideDataset: boolean;
  rowRefs: MutableRefObject<Map<number, HTMLTableRowElement>>;
  onToggleSelect: (id: number) => void;
  onToggleGroup: (ids: number[]) => void;
  onOpen: (exc: ExceptionRecord) => void;
  onPage: (nextOffset: number) => void;
}) {
  const [hidden, setHidden] = useState<string[]>(() => getPref<string[]>(PREF_KEYS.cols, []));
  const [gearOpen, setGearOpen] = useState(false);
  const gearRef = useRef<HTMLDivElement>(null);

  function isVisible(col: string): boolean {
    if (col === "dataset" && hideDataset) return false;
    return !hidden.includes(col);
  }
  function toggleCol(col: string) {
    const next = hidden.includes(col) ? hidden.filter((c) => c !== col) : [...hidden, col];
    setHidden(next);
    setPref(PREF_KEYS.cols, next);
  }

  // Client-side grouping of the *current page only* (server-side grouping is out
  // of scope v1 — see the issue). Preserves server order within each group.
  const groups = useMemo(() => {
    if (group === "none") return [{ key: "", label: "", rows: items }];
    const map = new Map<string, ExceptionRecord[]>();
    for (const e of items) {
      const key = group === "check" ? e.check_name : e.dataset_name;
      const arr = map.get(key) ?? [];
      arr.push(e);
      map.set(key, arr);
    }
    return [...map.entries()].map(([label, rows]) => ({ key: label, label, rows }));
  }, [items, group]);

  const colCount =
    1 + // checkbox (or spacer)
    2 + // severity + status
    1 + // reason
    (isVisible("dataset") ? 1 : 0) +
    (isVisible("check") ? 1 : 0) +
    (isVisible("occurrences") ? 1 : 0) +
    (isVisible("last_seen") ? 1 : 0) +
    (isVisible("assignee") ? 1 : 0);

  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + pageSize, offset + items.length);

  return (
    <div className="card table-wrap xw-table-card">
      <div className="xw-table-toolbar">
        <span className="xw-loading-bar" data-on={loading ? "1" : "0"} aria-hidden />
        <div className="xw-gear" ref={gearRef}>
          <button
            className="small ghost icon-only"
            aria-label="Column visibility"
            title="Columns"
            onClick={() => setGearOpen((o) => !o)}
          >
            <Icon name="settings" size={14} />
          </button>
          {gearOpen && (
            <div className="xw-gear-menu" role="menu">
              {TOGGLE_COLS.filter((c) => !(c.id === "dataset" && hideDataset)).map((c) => (
                <label key={c.id} className="xw-gear-item">
                  <input type="checkbox" checked={isVisible(c.id)} onChange={() => toggleCol(c.id)} />
                  {c.label}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      <table className="data xw-table">
        <thead>
          <tr>
            <th style={{ width: 30 }} aria-label="select" />
            <th>Severity</th>
            <th>Status</th>
            <th>Reason</th>
            {isVisible("dataset") && <th>Dataset</th>}
            {isVisible("check") && <th>Check</th>}
            {isVisible("occurrences") && <th className="num">Seen</th>}
            {isVisible("last_seen") && <th>Last seen</th>}
            {isVisible("assignee") && <th>Assignee</th>}
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr>
              <td colSpan={colCount} className="xw-empty-row">
                {loading ? "Loading…" : "No exceptions match these filters."}
              </td>
            </tr>
          )}
          {groups.map((g) => {
            const groupIds = g.rows.map((r) => r.id);
            const allSel = editable && groupIds.length > 0 && groupIds.every((id) => selected.has(id));
            return (
              <Fragment key={g.key || "_all"}>
                {group !== "none" && (
                  <tr className="xw-group-row">
                    <td>
                      {editable && (
                        <input
                          type="checkbox"
                          aria-label={`Select group ${g.label}`}
                          checked={allSel}
                          onChange={() => onToggleGroup(groupIds)}
                        />
                      )}
                    </td>
                    <td colSpan={colCount - 1}>
                      <strong>{g.label}</strong> <span className="xw-group-count">{g.rows.length}</span>
                    </td>
                  </tr>
                )}
                {g.rows.map((e) => {
                  const isSel = selected.has(e.id);
                  const isFocused = focusedId === e.id;
                  return (
                    <tr
                      key={e.id}
                      role="row"
                      aria-selected={isFocused}
                      tabIndex={-1}
                      ref={(el) => {
                        if (el) rowRefs.current.set(e.id, el);
                        else rowRefs.current.delete(e.id);
                      }}
                      className={`xw-row${isFocused ? " xw-focused" : ""}${isSel ? " xw-selected" : ""}`}
                      onClick={() => onOpen(e)}
                    >
                      <td onClick={(ev) => ev.stopPropagation()}>
                        {editable && (
                          <input
                            type="checkbox"
                            aria-label={`Select exception ${e.id}`}
                            checked={isSel}
                            onChange={() => onToggleSelect(e.id)}
                          />
                        )}
                      </td>
                      <td>
                        <SevBadge severity={e.check_severity} />
                      </td>
                      <td>
                        <StatusPill status={e.status} />
                      </td>
                      <td className="xw-reason-cell">
                        <div className="xw-reason">{e.reason}</div>
                        {e.note && <div className="xw-note-line">{e.note}</div>}
                      </td>
                      {isVisible("dataset") && <td className="xw-dim">{e.dataset_name}</td>}
                      {isVisible("check") && <td className="xw-dim">{e.check_name}</td>}
                      {isVisible("occurrences") && (
                        <td className="num">
                          {e.occurrence_count > 1 && <span className="xw-occ">×{e.occurrence_count}</span>}
                          {isRecent(e.first_seen_at) && <span className="xw-new-tint">new</span>}
                        </td>
                      )}
                      {isVisible("last_seen") && (
                        <td className="xw-dim xw-nowrap">{fmtRelative(e.last_seen_at)}</td>
                      )}
                      {isVisible("assignee") && (
                        <td className="xw-dim">{e.assigned_to ?? <span className="xw-muted">—</span>}</td>
                      )}
                    </tr>
                  );
                })}
              </Fragment>
            );
          })}
        </tbody>
      </table>

      <div className="xw-pager">
        <span className="xw-pager-info">
          {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}
        </span>
        <div className="xw-pager-btns">
          <button className="small" disabled={offset <= 0} onClick={() => onPage(Math.max(0, offset - pageSize))}>
            Prev
          </button>
          <button className="small" disabled={offset + pageSize >= total} onClick={() => onPage(offset + pageSize)}>
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

