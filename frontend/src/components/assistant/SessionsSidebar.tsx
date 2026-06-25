import type { ChatSession } from "../../api/types";
import { timeAgo } from "../../lib/format";
import { Icon, Spinner } from "../ui";

/** Conversation rail: new-conversation button + the session list with per-row delete.
 *  Presentational — the page owns selection, creation, and the delete confirmation. */
export function SessionsSidebar({
  sessions,
  loading,
  sessionId,
  editable,
  createPending,
  onSelect,
  onCreate,
  onDelete,
}: {
  sessions: ChatSession[];
  loading: boolean;
  sessionId: number | null;
  editable: boolean;
  createPending: boolean;
  onSelect: (id: number) => void;
  onCreate: () => void;
  onDelete: (session: ChatSession) => void;
}) {
  return (
    <aside className="chat-sessions">
      <button
        className="primary"
        style={{ width: "100%", justifyContent: "center" }}
        onClick={onCreate}
        disabled={createPending || !editable}
      >
        <Icon name="plus" size={14} /> New conversation
      </button>
      <div className="chat-session-list">
        {loading ? (
          <Spinner />
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              className={`chat-session-item${s.id === sessionId ? " active" : ""}`}
              onClick={() => onSelect(s.id)}
            >
              <div className="title">{s.title || "New conversation"}</div>
              <div className="meta">
                {s.message_count > 0 ? `${s.message_count} messages · ` : ""}
                {timeAgo(s.updated_at)}
              </div>
              <button
                className="ghost small del"
                title="Delete conversation"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(s);
                }}
              >
                <Icon name="x" size={12} />
              </button>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
