import type { MouseEvent } from "react";
import { Link } from "react-router";

import type { Dataset } from "../../api/types";
import { fmtNum, timeAgo } from "../../lib/format";
import { Icon, StatusPill } from "../ui";

/**
 * Dataset health — says whether a red dataset needs TRIAGE or REPAIR (#262).
 *
 * A check that ERRORED never evaluated the data: it writes no exceptions, so a
 * dataset whose checks all error rendered a red "fail" over an empty Exceptions
 * tab with no reason given.
 *
 * Two independent decisions, deliberately kept apart:
 *  - *Pill suppression* asks whether the verdict is owed to errors alone
 *    (`health === "fail"` with nothing actually failing). That is a statement
 *    about `health`, so it reads `failing_checks`.
 *  - *Wording* asks how much of the monitoring is down, which is exactly what the
 *    server's `monitoring` field says (`broken` = every active check errors,
 *    `degraded` = some still run). Deriving it from the suppression flag instead
 *    calls a degraded dataset "checks broken" — overstating the outage in the
 *    same dishonest direction #262 exists to remove.
 *
 * The chip links to the errored run, which carries the driver error and the
 * "test the source connection" remedy (PR #278). Shared with the dataset detail
 * header so the two surfaces cannot disagree one click apart.
 */
export function DatasetHealth({ d }: { d: Dataset }) {
  if (d.errored_checks <= 0) return <StatusPill value={d.health} />;
  const verdictIsOnlyErrors = d.health === "fail" && d.failing_checks <= 0;
  // Errored checks capture nothing new, but exceptions captured before the source
  // broke are still open and still triageable — don't tell the analyst otherwise
  // while the same row shows a non-zero open count.
  const triageClause =
    d.open_exceptions > 0
      ? `${fmtNum(d.open_exceptions)} open exception${d.open_exceptions === 1 ? "" : "s"} already captured still need triage`
      : "nothing to triage until that is fixed";
  const repairTitle = [
    `${fmtNum(d.errored_checks)} of ${fmtNum(d.active_checks)} active check${d.active_checks === 1 ? "" : "s"} could not run — ${triageClause}`,
    d.last_error,
  ]
    .filter(Boolean)
    .join(": ");
  // inline-flex, not flex: this also renders inside the detail page's <h1>.
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      {!verdictIsOnlyErrors && <StatusPill value={d.health} />}
      <Link
        to={d.last_error_run_id ? `/runs/${d.last_error_run_id}` : `/datasets/${d.id}/runs`}
        className="pill tone-warn"
        title={repairTitle}
        onClick={(e) => e.stopPropagation()}
      >
        {d.monitoring === "broken" ? "checks broken" : `${fmtNum(d.errored_checks)} not running`}
      </Link>
    </span>
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
                <DatasetHealth d={d} />
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
