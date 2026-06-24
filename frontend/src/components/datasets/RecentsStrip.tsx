import type { Dataset } from "../../api/types";

/** "Recently viewed" dataset chips. Resolved against the live list by the page, so
 *  only datasets that still exist appear. Renders nothing when there are none. */
export function RecentsStrip({
  datasets,
  onOpen,
}: {
  datasets: Dataset[];
  onOpen: (id: number) => void;
}) {
  if (datasets.length === 0) return null;
  return (
    <div className="recents-strip">
      <span className="recents-label">Recently viewed</span>
      <div className="chip-row">
        {datasets.map((d) => (
          <button
            key={d.id}
            type="button"
            className="filter-chip"
            onClick={() => onOpen(d.id)}
            title={`${d.connection_name} — open dataset`}
          >
            {d.schema_name ? `${d.schema_name}.` : ""}
            {d.table_name}
          </button>
        ))}
      </div>
    </div>
  );
}
