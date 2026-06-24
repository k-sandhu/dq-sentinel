import { type MouseEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { DatasetsTable } from "../components/datasets/DatasetsTable";
import { DatasetsToolbar } from "../components/datasets/DatasetsToolbar";
import {
  type HealthFilter,
  type RollupFilterKey,
  filterAndSortDatasets,
  parseRollupFilters,
  resolveRecentDatasets,
} from "../components/datasets/datasetsFilters";
import { RecentsStrip } from "../components/datasets/RecentsStrip";
import { RollupFilterStrip } from "../components/datasets/RollupFilterStrip";
import { useDatasets } from "../components/datasets/useDatasets";
import { EmptyState, ErrorBox, Spinner } from "../components/ui";
import {
  getFavorites,
  getRecents,
  pruneStalePrefs,
  subscribePrefs,
  toggleFavorite,
} from "../lib/prefs";

/**
 * Datasets index (D4 / FE-2). A thin shell that owns the UI state (search, health
 * filter, favorites, recents) and composes the extracted feature pieces; the data
 * fetch lives in `useDatasets`, and the filter/sort brain in `datasetsFilters`.
 */
export default function DatasetsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { domainFilter, teamFilter, activeRollupFilters } = parseRollupFilters(searchParams);

  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [healthFilter, setHealthFilter] = useState<HealthFilter>("all");
  // Snapshot of prefs; re-read on any in-tab change so stars/recents stay live.
  const [favIds, setFavIds] = useState<number[]>(() => getFavorites());
  const [recents, setRecents] = useState(() => getRecents());
  useEffect(
    () =>
      subscribePrefs(() => {
        setFavIds(getFavorites());
        setRecents(getRecents());
      }),
    [],
  );

  const { data: raw, isLoading, error } = useDatasets();

  // Prune favorites/recents pointing at deleted datasets once the live list loads.
  useEffect(() => {
    if (raw) pruneStalePrefs(raw.map((d) => d.id));
  }, [raw]);

  const favSet = useMemo(() => new Set(favIds), [favIds]);
  const data = useMemo(
    () => filterAndSortDatasets(raw, { search, healthFilter, favSet, domainFilter, teamFilter }),
    [raw, search, healthFilter, favSet, domainFilter, teamFilter],
  );
  const recentDatasets = useMemo(() => resolveRecentDatasets(raw, recents), [raw, recents]);

  function onToggleFav(e: MouseEvent, id: number) {
    e.stopPropagation(); // don't navigate into the row
    toggleFavorite(id); // dq:prefs event refreshes favIds via the subscription
  }

  function clearRollupFilter(key: RollupFilterKey) {
    const next = new URLSearchParams(searchParams);
    next.delete(key);
    setSearchParams(next);
  }

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
        <Link to="/connections" className="btn">
          Browse sources
        </Link>
      </div>

      <DatasetsToolbar
        search={search}
        onSearch={setSearch}
        healthFilter={healthFilter}
        onHealthFilter={setHealthFilter}
      />
      <RollupFilterStrip filters={activeRollupFilters} onClear={clearRollupFilter} />
      <ErrorBox error={error} />
      <RecentsStrip datasets={recentDatasets} onOpen={(id) => navigate(`/datasets/${id}`)} />

      {isLoading ? (
        <Spinner />
      ) : !raw?.length ? (
        <div className="card">
          <EmptyState
            title="No datasets registered"
            hint="Open a connection and register tables to monitor them."
          >
            <Link to="/connections" className="btn primary">
              Go to connections
            </Link>
          </EmptyState>
        </div>
      ) : (
        <DatasetsTable
          data={data}
          favSet={favSet}
          onToggleFav={onToggleFav}
          onNavigate={(id) => navigate(`/datasets/${id}`)}
        />
      )}
    </div>
  );
}
