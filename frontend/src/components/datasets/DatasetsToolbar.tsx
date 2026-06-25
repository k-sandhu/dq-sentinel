import { HEALTH_FILTERS, type HealthFilter } from "./datasetsFilters";

/** Search box + health filter chips. Presentational — all state lives in the page. */
export function DatasetsToolbar({
  search,
  onSearch,
  healthFilter,
  onHealthFilter,
}: {
  search: string;
  onSearch: (value: string) => void;
  healthFilter: HealthFilter;
  onHealthFilter: (value: HealthFilter) => void;
}) {
  return (
    <div className="toolbar">
      <input
        type="text"
        aria-label="Search datasets"
        placeholder="Search by table, connection, owner, domain or team…"
        value={search}
        onChange={(e) => onSearch(e.target.value)}
        style={{ maxWidth: 300, marginTop: 0 }}
      />
      <div className="chip-row">
        {HEALTH_FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            className={`filter-chip${healthFilter === f ? " on" : ""}`}
            aria-pressed={healthFilter === f}
            onClick={() => onHealthFilter(f)}
          >
            {f}
          </button>
        ))}
      </div>
    </div>
  );
}
