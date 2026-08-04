import type { MouseEvent } from "react";
import { Link } from "react-router";

import type { Dataset } from "../../api/types";
import { fmtNum, timeAgo } from "../../lib/format";
import { Icon, StatusPill } from "../ui";

/**
 * Health cell — says whether a red row needs TRIAGE or REPAIR (#262).
 *
 * A check that ERRORED never evaluated the data: it writes no exceptions, so a
 * dataset whose checks all error rendered a red "fail" over an empty Exceptions
 * tab with no reason given. The red verdict is suppressed only when it is
 * *entirely* attributable to errored checks (`health === "fail"` with nothing
 * actually failing) — a real fail or warn keeps its pill and gains a repair chip
 * beside it. Both chips link to the errored run, which already carries the driver
 * error and the "test the source connection" remedy (PR #278).
 */
function HealthCell({ d }: { d: Dataset }) {
  if (d.errored_checks <= 0) return <StatusPill value={d.health} />;
  const verdictIsOnlyErrors = d.health === "fail" && d.failing_checks <= 0;
  const repairTitle = [
    `${d.errored_checks} of ${d.active_checks} active check${d.active_checks === 1 ? "" : "s"} could not run — nothing to triage until that is fixed`,
    d.last_error,
  ]
    .filter(Boolean)
    .join(": ");
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      {!verdictIsOnlyErrors && <StatusPill value={d.health} />}
      <Link
        to={d.last_error_run_id ? `/runs/${d.last_error_run_id}` : `/datasets/${d.id}/runs`}
        className="pill tone-warn"
        title={repairTitle}
        onClick={(e) => e.stopPropagation()}
      >
        {verdictIsOnlyErrors ? "checks broken" : `${fmtNum(d.errored_checks)} not running`}
      </Link>
    </div>
  );
}

/** The datasets table. Presentational: the page owns the data, favorites set, and
 *  navigation; this renders rows and surfaces the star toggle + row links. */
export function DatasetsTable({
  data,
  favSet,
  onToggleFav,
  onNavigate,
}: {
  data: Dataset[];
  favSet: Set<number>;
  onToggleFav: (e: MouseEvent, id: number) => void;
  onNavigate: (id: number) => void;
}) {
  return (
    <div className="card table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th className="star-col" aria-label="Favorite" />
            <th>Health</th>
            <th>Dataset</th>
            <th>Connection</th>
            <th>Owner</th>
            <th>Domain / team</th>
            <th>Importance</th>
            <th className="num">Rows</th>
            <th className="num">Active checks</th>
            <th className="num">Open exceptions</th>
            <th>Last profiled</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.id} className="clickable" onClick={() => onNavigate(d.id)}>
              <td className="star-col">
                <button
                  type="button"
                  className="ghost icon-only star-btn"
                  aria-pressed={favSet.has(d.id)}
                  aria-label={
                    favSet.has(d.id)
                      ? `Remove ${d.table_name} from favorites`
                      : `Add ${d.table_name} to favorites`
                  }
                  title={favSet.has(d.id) ? "Remove from favorites" : "Add to favorites"}
                  onClick={(e) => onToggleFav(e, d.id)}
                >
                  <Icon name={favSet.has(d.id) ? "star-filled" : "star"} size={15} />
                </button>
              </td>
              <td>
                <HealthCell d={d} />
              </td>
              <td style={{ fontWeight: 700 }}>
                <Link
                  to={`/datasets/${d.id}`}
                  className="row-title-link"
                  onClick={(e) => e.stopPropagation()}
                >
                  {d.schema_name ? `${d.schema_name}.` : ""}
                  {d.table_name}
                </Link>
              </td>
              <td style={{ color: "var(--text-light)" }}>{d.connection_name}</td>
              <td style={{ color: "var(--text-light)", fontSize: 12 }}>{d.owner ?? "—"}</td>
              <td style={{ color: "var(--text-light)", fontSize: 12 }}>
                {[d.domain, d.team].filter(Boolean).join(" / ") || "—"}
              </td>
              <td>
                {d.importance ? (
                  <span
                    className="badge"
                    style={
                      d.importance === "critical" || d.importance === "high"
                        ? { borderColor: "var(--danger)", color: "var(--danger-dark)" }
                        : undefined
                    }
                  >
                    {d.importance}
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td className="num">{fmtNum(d.row_count)}</td>
              <td className="num">{fmtNum(d.active_checks)}</td>
              <td
                className="num"
                style={{
                  color: d.open_exceptions ? "var(--danger-dark)" : undefined,
                  fontWeight: d.open_exceptions ? 700 : 400,
                }}
              >
                {fmtNum(d.open_exceptions)}
              </td>
              <td style={{ color: "var(--text-light)" }}>{timeAgo(d.last_profiled_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
