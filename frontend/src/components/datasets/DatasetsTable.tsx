import type { MouseEvent } from "react";
import { Link } from "react-router";

import type { Dataset } from "../../api/types";
import { fmtNum, timeAgo } from "../../lib/format";
import { Icon, StatusPill } from "../ui";

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
                <StatusPill value={d.health} />
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
