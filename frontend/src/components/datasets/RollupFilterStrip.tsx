import type { ActiveRollupFilter, RollupFilterKey } from "./datasetsFilters";

/** The active-filter strip: clearable domain/team chips mirroring the URL rollup
 *  filters a drill-in set (Home's risk worklist links here via `rollupFilterHref`;
 *  scorecard rollups will too). Renders nothing when no filter is active. */
export function RollupFilterStrip({
  filters,
  onClear,
}: {
  filters: ActiveRollupFilter[];
  onClear: (key: RollupFilterKey) => void;
}) {
  if (filters.length === 0) return null;
  return (
    <div className="dataset-filter-strip" aria-label="Active dataset filters">
      <span className="recents-label">Filtered by</span>
      <div className="chip-row">
        {filters.map((filter) => (
          <button
            key={filter.key}
            type="button"
            className="filter-chip on"
            onClick={() => onClear(filter.key)}
            aria-label={`Clear ${filter.label.toLowerCase()} filter: ${filter.value}`}
            title={`Clear ${filter.label.toLowerCase()} filter`}
          >
            {filter.label}: {filter.value} <span aria-hidden="true">✕</span>
          </button>
        ))}
      </div>
    </div>
  );
}
