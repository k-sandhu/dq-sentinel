import type { QueryHistoryEntry } from "../../lib/queryHistory";
import { fmtNum, timeAgo } from "../../lib/format";
import { Modal } from "../ui";

/** This-browser query history: re-edit or re-run a past query, or clear the log. */
export function HistoryModal({
  history,
  editable,
  onClose,
  onLoad,
  onClear,
}: {
  history: QueryHistoryEntry[];
  editable: boolean;
  onClose: () => void;
  onLoad: (entry: QueryHistoryEntry, thenRun: boolean) => void;
  onClear: () => void;
}) {
  return (
    <Modal
      title="Query history"
      onClose={onClose}
      wide
      footer={
        <>
          <button className="ghost" onClick={onClose}>Close</button>
          {history.length > 0 && (
            <button
              className="ghost small danger"
              onClick={() => {
                if (window.confirm("Clear this browser's query history?")) onClear();
              }}
            >
              Clear history
            </button>
          )}
        </>
      }
    >
      {history.length === 0 ? (
        <div className="empty" style={{ padding: 18 }}>No queries run yet in this browser.</div>
      ) : (
        <div style={{ maxHeight: "60vh", overflowY: "auto" }}>
          {history.map((h) => (
            <div key={h.id} className="insight" style={{ borderLeftColor: h.ok ? "var(--ok)" : "var(--danger)", padding: "8px 12px" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <span style={{ fontSize: 11.5, color: "var(--text-light)" }}>
                  {timeAgo(h.ranAt)}{h.connectionName ? ` · ${h.connectionName}` : ""}
                </span>
                <span style={{ marginLeft: "auto", fontSize: 11.5, color: h.ok ? "var(--text-light)" : "var(--danger)" }}>
                  {h.ok ? `${fmtNum(h.rowCount)} rows · ${h.elapsedMs} ms` : "error"}
                </span>
              </div>
              <pre className="result" style={{ maxHeight: 110, fontSize: 11, marginTop: 4 }}>{h.sql}</pre>
              {!h.ok && h.error && <div style={{ fontSize: 11, color: "var(--danger)" }}>{h.error}</div>}
              <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                <button className="small" onClick={() => onLoad(h, false)}>Edit</button>
                {editable && <button className="primary small" onClick={() => onLoad(h, true)}>Run</button>}
              </div>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
