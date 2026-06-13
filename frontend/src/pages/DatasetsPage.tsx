import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type { Dataset } from "../api/types";
import { EmptyState, ErrorBox, Icon, Spinner, StatusPill } from "../components/ui";
import { fmtNum, timeAgo } from "../lib/format";
import {
  getFavoriteDatasetIds,
  getRecentDatasets,
  pruneDatasetPrefs,
  subscribePrefs,
  toggleFavoriteDataset,
} from "../lib/prefs";

const HEALTH_FILTERS = ["all", "fail", "warn", "pass", "unknown"] as const;

function datasetLabel(dataset: Dataset): string {
  return dataset.display_name || `${dataset.schema_name ? `${dataset.schema_name}.` : ""}${dataset.table_name}`;
}

export default function DatasetsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [healthFilter, setHealthFilter] = useState<(typeof HEALTH_FILTERS)[number]>("all");
  const [favorites, setFavorites] = useState<number[]>(() => getFavoriteDatasetIds());
  const [recents, setRecents] = useState(() => getRecentDatasets());
  const { data: raw, isLoading, error } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });

  useEffect(
    () =>
      subscribePrefs(() => {
        setFavorites(getFavoriteDatasetIds());
        setRecents(getRecentDatasets());
      }),
    [],
  );

  useEffect(() => {
    if (!raw) return;
    const pruned = pruneDatasetPrefs(raw.map((dataset) => dataset.id));
    setFavorites(pruned.favorites);
    setRecents(pruned.recents);
  }, [raw]);

  const datasetsById = useMemo(() => new Map((raw ?? []).map((dataset) => [dataset.id, dataset])), [raw]);
  const favoriteIds = useMemo(() => new Set(favorites), [favorites]);
  const recentDatasets = useMemo(
    () => recents.map((recent) => datasetsById.get(recent.id)).filter((dataset): dataset is Dataset => Boolean(dataset)),
    [datasetsById, recents],
  );

  const data = useMemo(() => {
    let list = raw ?? [];
    if (search) {
      const needle = search.toLowerCase();
      list = list.filter(
        (d) =>
          d.table_name.toLowerCase().includes(needle) ||
          d.connection_name.toLowerCase().includes(needle) ||
          (d.owner ?? "").toLowerCase().includes(needle),
      );
    }
    if (healthFilter !== "all") list = list.filter((d) => (d.health ?? "unknown") === healthFilter);
    const favoriteRank = new Map(favorites.map((id, index) => [id, index]));
    return [...list].sort((a, b) => {
      const aRank = favoriteRank.get(a.id);
      const bRank = favoriteRank.get(b.id);
      if (aRank !== undefined || bRank !== undefined) {
        if (aRank === undefined) return 1;
        if (bRank === undefined) return -1;
        return aRank - bRank;
      }
      return b.open_exceptions - a.open_exceptions || datasetLabel(a).localeCompare(datasetLabel(b));
    });
  }, [raw, search, healthFilter, favorites]);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Datasets</h1>
          <div className="sub">
            Tables and views under data-quality monitoring
            {raw ? ` · ${data.length} of ${raw.length} shown` : ""}
          </div>
        </div>
        <Link to="/connections" className="btn">Browse sources</Link>
      </div>
      <div className="toolbar">
        <input
          type="text"
          placeholder="Search by table, connection or owner…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: 300, marginTop: 0 }}
        />
        <div className="chip-row">
          {HEALTH_FILTERS.map((f) => (
            <button key={f} className={`filter-chip${healthFilter === f ? " on" : ""}`} onClick={() => setHealthFilter(f)}>
              {f}
            </button>
          ))}
        </div>
      </div>
      <ErrorBox error={error} />
      {isLoading ? (
        <Spinner />
      ) : !raw?.length ? (
        <div className="card">
          <EmptyState title="No datasets registered" hint="Open a connection and register tables to monitor them.">
            <Link to="/connections" className="btn primary" style={{ background: "var(--brand)", color: "#fff" }}>
              Go to connections
            </Link>
          </EmptyState>
        </div>
      ) : (
        <>
          {recentDatasets.length > 0 && (
            <div className="recent-strip">
              <span className="recent-label">Recently viewed</span>
              <div className="chip-row">
                {recentDatasets.map((dataset) => (
                  <button
                    key={dataset.id}
                    type="button"
                    className="filter-chip recent-chip"
                    title={`${datasetLabel(dataset)} on ${dataset.connection_name}`}
                    onClick={() => navigate(`/datasets/${dataset.id}`)}
                  >
                    {datasetLabel(dataset)}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="card table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th className="favorite-col" aria-label="Favorite" />
                <th>Health</th>
                <th>Dataset</th>
                <th>Connection</th>
                <th>Owner</th>
                <th>Importance</th>
                <th className="num">Rows</th>
                <th className="num">Active checks</th>
                <th className="num">Open exceptions</th>
                <th>Last profiled</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d) => {
                const isFavorite = favoriteIds.has(d.id);
                return (
                <tr key={d.id} className="clickable" onClick={() => navigate(`/datasets/${d.id}`)}>
                  <td className="favorite-col">
                    <button
                      type="button"
                      className={`favorite-toggle${isFavorite ? " on" : ""}`}
                      aria-label={`${isFavorite ? "Remove" : "Add"} ${datasetLabel(d)} ${isFavorite ? "from" : "to"} favorites`}
                      aria-pressed={isFavorite}
                      title={isFavorite ? "Remove from favorites" : "Add to favorites"}
                      onClick={(event) => {
                        event.stopPropagation();
                        setFavorites(toggleFavoriteDataset(d.id));
                      }}
                    >
                      <Icon name={isFavorite ? "star-filled" : "star"} size={15} />
                    </button>
                  </td>
                  <td><StatusPill value={d.health} /></td>
                  <td style={{ fontWeight: 700, color: "var(--text-dark)" }}>
                    {d.schema_name ? `${d.schema_name}.` : ""}
                    {d.table_name}
                  </td>
                  <td style={{ color: "var(--text-light)" }}>{d.connection_name}</td>
                  <td style={{ color: "var(--text-light)", fontSize: 12 }}>{d.owner ?? "—"}</td>
                  <td>
                    {d.importance ? (
                      <span className="badge" style={d.importance === "critical" || d.importance === "high" ? { borderColor: "var(--danger)", color: "var(--danger-dark)" } : undefined}>
                        {d.importance}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="num">{fmtNum(d.row_count)}</td>
                  <td className="num">{fmtNum(d.active_checks)}</td>
                  <td className="num" style={{ color: d.open_exceptions ? "var(--danger-dark)" : undefined, fontWeight: d.open_exceptions ? 700 : 400 }}>
                    {fmtNum(d.open_exceptions)}
                  </td>
                  <td style={{ color: "var(--text-light)" }}>{timeAgo(d.last_profiled_at)}</td>
                </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </>
      )}
    </div>
  );
}
