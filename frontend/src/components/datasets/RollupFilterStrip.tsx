import type { ActiveRollupFilter, RollupFilterKey } from "./datasetsFilters";

/** The "Filtered by scorecard" strip — clearable domain/team chips that mirror the
 *  URL rollup filters a scorecard drill-in sets. Renders nothing when none active. */
export function RollupFilterStrip({
  filters,
  onClear,
}: {
  filters: ActiveRollupFilter[];
  onClear: (key: RollupFilterKey) => void;
}) {
  if (filters.length === 0) return null;
  return (
    <div className="dataset-filter-strip">
      <span className="recents-label">Filtered by scorecard</span>
      <div className="chip-row">
        {filters.map((filter) => (
          <button
            key={filter.key}
            type="button"
            className="filter-chip on"
            onClick={() => onClear(filter.key)}
            title={`Clear ${filter.label.toLowerCase()} filter`}
          >
            {filter.label}: {filter.value} ✕
          </button>
        ))}
      </div>
    </div>
  );
}
