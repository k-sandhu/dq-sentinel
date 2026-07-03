import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { SavedQuery } from "../../api/types";
import { useAuth } from "../../auth";
import { useConfirm } from "../confirm";
import { ErrorBox, Icon, Spinner } from "../ui";
import { canManageQuery } from "./shared";

/** Saved-query library for the active connection: search, tag filter, load/run, and
 *  creator/admin-gated delete. */
export function SavedQueriesRail({
  connectionId,
  editable,
  onLoad,
  onRun,
}: {
  connectionId: number;
  editable: boolean;
  onLoad: (q: SavedQuery) => void;
  onRun: (q: SavedQuery) => void;
}) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [search, setSearch] = useState("");
  const [activeTag, setActiveTag] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: qk.savedQueries.byConnection(connectionId),
    queryFn: () => api.get<SavedQuery[]>(`/queries?connection_id=${connectionId}`),
    staleTime: 15_000,
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/queries/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.savedQueries.all }),
  });

  const allTags = useMemo(() => {
    const s = new Set<string>();
    (data ?? []).forEach((q) => q.tags.forEach((t) => s.add(t)));
    return [...s].sort();
  }, [data]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (data ?? []).filter((q) => {
      if (activeTag && !q.tags.includes(activeTag)) return false;
      if (!needle) return true;
      return (
        q.name.toLowerCase().includes(needle) ||
        q.description.toLowerCase().includes(needle)
      );
    });
  }, [data, search, activeTag]);

  return (
    <div className="card card-pad" style={{ marginTop: 14 }}>
      <h3 style={{ marginBottom: 8 }}>Saved queries</h3>
      <input
        type="text"
        aria-label="Search saved queries"
        value={search}
        placeholder="Search name / description"
        onChange={(e) => setSearch(e.target.value)}
        style={{ marginTop: 0, fontSize: 12.5 }}
      />
      {allTags.length > 0 && (
        <div className="chip-row" style={{ marginTop: 8 }}>
          {allTags.map((t) => (
            <button
              key={t}
              className={`filter-chip${activeTag === t ? " on" : ""}`}
              onClick={() => setActiveTag(activeTag === t ? null : t)}
            >
              {t}
            </button>
          ))}
        </div>
      )}
      <ErrorBox error={error} />
      <div style={{ marginTop: 10, maxHeight: "48vh", overflowY: "auto" }}>
        {isLoading ? (
          <Spinner label="Loading…" />
        ) : !filtered.length ? (
          <div className="empty" style={{ padding: 14, fontSize: 12.5 }}>
            {data?.length ? "No queries match your filter." : "No saved queries yet. Run SQL and click Save."}
          </div>
        ) : (
          filtered.map((q) => (
            <div key={q.id} className="insight" style={{ padding: "8px 12px" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                <button
                  className="ghost small"
                  style={{ flex: 1, justifyContent: "flex-start", fontWeight: 700, padding: 0, textAlign: "left" }}
                  title="Load into the editor"
                  onClick={() => onLoad(q)}
                >
                  {q.name}
                </button>
                {editable && (
                  <button className="ghost small" title="Load and run" onClick={() => onRun(q)}>
                    <Icon name="play" size={12} />
                  </button>
                )}
                {canManageQuery(user, q) && (
                  <button
                    className="ghost small danger"
                    title="Delete"
                    disabled={remove.isPending}
                    onClick={async () => {
                      if (
                        await confirm({
                          title: "Delete saved query?",
                          body: `“${q.name}” will be removed for everyone who can see it.`,
                          confirmLabel: "Delete",
                          danger: true,
                        })
                      )
                        remove.mutate(q.id);
                    }}
                  >
                    <Icon name="x" size={12} />
                  </button>
                )}
              </div>
              {q.description && (
                <div style={{ fontSize: 11, color: "var(--text-light)", margin: "1px 0 4px" }}>{q.description}</div>
              )}
              {(q.tags.length > 0 || q.dataset_id) && (
                <div className="chip-row" style={{ marginTop: 2 }}>
                  {q.dataset_id && <span className="badge kind">pinned</span>}
                  {q.tags.map((t) => (
                    <span key={t} className="badge" style={{ fontSize: 9.5 }}>{t}</span>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
