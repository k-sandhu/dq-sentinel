import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Check, CheckVersion } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { describeSchedule, fmtDateTime } from "../lib/format";
import { useConfirm } from "./confirm";
import { EmptyState, ErrorBox, Icon, SeverityBadge, Spinner } from "./ui";

function paramsSummary(params: Record<string, unknown>): string {
  const entries = Object.entries(params ?? {});
  if (!entries.length) return "no params";
  return entries
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(", ");
}

// Version history + one-click rollback for a single check (#185). Shared by the
// check detail page (as a card) and the checks table (inside a modal).
export default function CheckHistory({ checkId, onRestored }: { checkId: number; onRestored?: () => void }) {
  const qc = useQueryClient();
  const { user } = useAuth();
  const editable = canEdit(user);
  const confirm = useConfirm();

  const { data: versions, isLoading, error } = useQuery({
    queryKey: ["check-versions", checkId],
    queryFn: () => api.get<CheckVersion[]>(`/checks/${checkId}/versions`),
  });

  const restore = useMutation({
    mutationFn: (version: number) => api.post<Check>(`/checks/${checkId}/restore`, { version }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["checks"] });
      qc.invalidateQueries({ queryKey: ["check-versions", checkId] });
      qc.invalidateQueries({ queryKey: ["runs"] });
      onRestored?.();
    },
  });

  if (isLoading) return <Spinner label="Loading history…" />;
  if (error) return <ErrorBox error={error} />;
  if (!versions?.length) {
    return <EmptyState title="No version history" hint="Edits to this check will appear here." />;
  }

  return (
    <>
      <ErrorBox error={restore.error} />
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th style={{ width: 44 }}>Ver</th>
              <th>Change</th>
              <th>Configuration</th>
              <th>When</th>
              {editable && <th style={{ textAlign: "right" }}>Action</th>}
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.id}>
                <td>
                  <span className="badge">v{v.version}</span>
                </td>
                <td>
                  <div style={{ fontWeight: 600 }}>
                    {v.change_note || "—"}
                    {v.is_current && (
                      <span style={{ color: "var(--ok)", fontWeight: 700, fontSize: 11, marginLeft: 6 }}>● current</span>
                    )}
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--text-light)" }}>{v.created_by ?? "system"}</div>
                </td>
                <td style={{ fontSize: 12 }}>
                  <div>
                    <SeverityBadge severity={v.severity} />{" "}
                    {v.column_name ? <code>{v.column_name}</code> : <span style={{ color: "var(--text-light)" }}>table-level</span>}
                  </div>
                  <div className="mono" style={{ color: "var(--text-light)", overflowWrap: "anywhere" }}>
                    {paramsSummary(v.params)}
                  </div>
                  <div style={{ color: "var(--text-light)" }}>{describeSchedule(v.schedule_kind, v.schedule_expr)}</div>
                </td>
                <td style={{ whiteSpace: "nowrap", fontSize: 12, color: "var(--text-light)" }}>
                  {fmtDateTime(v.created_at)}
                </td>
                {editable && (
                  <td style={{ textAlign: "right" }}>
                    {!v.is_current && (
                      <button
                        className="small"
                        disabled={restore.isPending}
                        onClick={async () => {
                          if (
                            await confirm({
                              title: `Restore version ${v.version}?`,
                              confirmLabel: "Restore",
                              body: (
                                <>
                                  Replace the check's current configuration with <strong>version {v.version}</strong>?
                                  The current state stays in history, so you can undo this.
                                </>
                              ),
                            })
                          )
                            restore.mutate(v.version);
                        }}
                      >
                        <Icon name="refresh" size={12} /> Restore
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
