import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../../api/client";
import type {
  CheckMatrixCell,
  CheckMatrixOut,
  StatusMatrixWidget as StatusMatrixWidgetT,
} from "../../api/types";
import { EmptyState, ErrorBox, Spinner } from "../ui";

// Glyph + heat-cell class per status. Glyph carries the semantics so the cell is
// never color-alone (epic accessibility standard). `error` reuses the fail color
// with an extra ring (`is-error`).
const CELL: Record<string, { glyph: string; cls: string; label: string }> = {
  pass: { glyph: "✓", cls: "ok", label: "pass" },
  warn: { glyph: "!", cls: "warn", label: "warn" },
  fail: { glyph: "✕", cls: "fail", label: "fail" },
  error: { glyph: "✕", cls: "fail is-error", label: "error" },
};

function cellMeta(cell: CheckMatrixCell) {
  if (cell.status == null) return { glyph: "·", cls: "neutral", label: "no run" };
  return CELL[cell.status] ?? { glyph: "·", cls: "neutral", label: cell.status };
}

function buildQuery(checkIds: number[], days: number): string {
  const sp = new URLSearchParams();
  checkIds.forEach((id) => sp.append("check_ids", String(id)));
  sp.set("days", String(days));
  return sp.toString();
}

/** Checks (possibly across datasets/connections) × recent UTC days, ✓/✗ per day.
 *  Renders the API result exactly (one aggregation rule, owned by the backend) as
 *  a real <table> for screen-reader navigation. Each cell links to that check's
 *  runs for the day; the row name opens the check's dataset checks tab. */
export default function StatusMatrixWidget({ widget }: { widget: StatusMatrixWidgetT }) {
  const { check_ids, days } = widget.config;
  const qs = buildQuery(check_ids, days);

  const { data, isLoading, error } = useQuery({
    queryKey: ["widget-matrix", qs],
    queryFn: () => api.get<CheckMatrixOut>(`/insights/check-matrix?${qs}`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
    enabled: check_ids.length > 0,
  });

  if (check_ids.length === 0) {
    return <EmptyState title="No checks selected" hint="Configure this widget to pick the checks to track." />;
  }
  if (error) return <ErrorBox error={error} />;
  if (isLoading && !data) return <Spinner label="Loading status matrix…" />;
  if (!data) return null;

  const { columns, rows } = data;
  // Configured checks the API didn't return (deleted/archived) — degrade visibly.
  const missing = new Set(check_ids).size - rows.length;

  return (
    <div className="cd-matrix-wrap">
      <div className="cd-matrix-note">
        Worst run status per <strong>UTC</strong> day — ✓ pass · <span aria-hidden>!</span> warn · ✕ fail/error · · no run.
      </div>
      {missing > 0 && (
        <div className="cd-matrix-warn">
          {missing} configured {missing === 1 ? "check" : "checks"} no longer{" "}
          {missing === 1 ? "exists" : "exist"} — edit widget.
        </div>
      )}
      <div className="cd-matrix table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col" className="cd-matrix-rowhead">
                Check
              </th>
              {columns.map((d) => (
                <th key={d} scope="col" title={`${d} (UTC)`}>
                  {d.slice(5)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.check_id}>
                <th scope="row" className="cd-matrix-rowhead">
                  <Link to={`/datasets/${row.dataset_id}/checks`} className="cd-matrix-check" title={row.check_name}>
                    {row.check_name}
                  </Link>
                  <div className="cd-matrix-rowmeta">
                    {row.dataset_name} · {row.connection_name}
                  </div>
                </th>
                {row.cells.map((cell, i) => {
                  const m = cellMeta(cell);
                  const date = columns[i];
                  const title = `${row.check_name}: ${m.label} on ${date} (${cell.runs} run${cell.runs === 1 ? "" : "s"})`;
                  return (
                    <td key={date} className="cd-matrix-td">
                      {cell.runs > 0 ? (
                        <Link
                          to={`/runs?check_id=${row.check_id}&day=${date}`}
                          className={`heat-cell ${m.cls} cd-matrix-cell`}
                          title={title}
                          aria-label={title}
                        >
                          {m.glyph}
                        </Link>
                      ) : (
                        <span className={`heat-cell ${m.cls} cd-matrix-cell`} title={title} aria-label={title}>
                          {m.glyph}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
