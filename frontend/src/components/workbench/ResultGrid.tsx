// Workbench result grid (#104): a real data grid over a {columns, rows} result,
// built on headless TanStack Table (pairs with the TanStack Query we already use).
// Provides per-column sort + filter, resizable + sticky headers, NULL styling, and
// click-to-expand of long cell values with copy (cell / row). Copy-all and CSV/JSON
// export live in the page toolbar (lib/csv.ts). Result sets are bounded by the
// query LIMIT (<=2000), so rows render without virtualization.

import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import type { ColumnDef, ColumnFiltersState, SortingState } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import type { QueryRunResult } from "../../api/types";
import { Icon, Modal } from "../ui";

type Row = unknown[];

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Display form for a non-null scalar: integers grouped, floats fixed, objects as
 *  JSON. NULL is handled by the caller so it can be styled distinctly. */
function display(value: unknown): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : String(value);
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    /* clipboard blocked (insecure context / permissions) — silently no-op */
  }
}

interface ExpandedCell {
  column: string;
  value: unknown;
  row: Row;
  columns: string[];
}

export default function ResultGrid({
  result,
  showFilters,
}: {
  result: QueryRunResult;
  showFilters: boolean;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [expanded, setExpanded] = useState<ExpandedCell | null>(null);

  const columns = useMemo<ColumnDef<Row>[]>(
    () =>
      result.columns.map((name, index) => ({
        id: `${index}:${name}`,
        header: name,
        accessorFn: (row) => row[index],
        enableResizing: true,
        sortingFn: (a, b, columnId) => {
          const av = a.getValue(columnId);
          const bv = b.getValue(columnId);
          if (typeof av === "number" && typeof bv === "number") return av - bv;
          return asText(av).localeCompare(asText(bv), undefined, { numeric: true });
        },
        filterFn: (row, columnId, filterValue) =>
          asText(row.getValue(columnId))
            .toLowerCase()
            .includes(String(filterValue).toLowerCase()),
      })),
    [result.columns],
  );

  const table = useReactTable<Row>({
    data: result.rows as Row[],
    columns,
    state: { sorting, columnFilters },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    columnResizeMode: "onChange",
    enableColumnResizing: true,
    defaultColumn: { minSize: 60, size: 170 },
  });

  const rows = table.getRowModel().rows;
  const visibleCount = rows.length;
  const filtered = columnFilters.length > 0 && visibleCount !== result.row_count;

  return (
    <>
      <div className="result-grid table-wrap" style={{ maxHeight: 460, overflow: "auto" }}>
        <table style={{ width: table.getCenterTotalSize() + 48 }}>
          <thead>
            <tr>
              <th className="grid-rownum">#</th>
              {table.getHeaderGroups()[0].headers.map((header) => {
                const sorted = header.column.getIsSorted();
                return (
                  <th key={header.id} style={{ width: header.getSize() }}>
                    <button
                      type="button"
                      className="grid-th-btn"
                      onClick={header.column.getToggleSortingHandler()}
                      title="Sort"
                    >
                      <span className="grid-th-label">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </span>
                      <span className="grid-sort">{sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : ""}</span>
                    </button>
                    <span
                      role="separator"
                      aria-orientation="vertical"
                      className={`grid-resizer${header.column.getIsResizing() ? " resizing" : ""}`}
                      onMouseDown={header.getResizeHandler()}
                      onTouchStart={header.getResizeHandler()}
                    />
                  </th>
                );
              })}
            </tr>
            {showFilters && (
              <tr className="grid-filter-row">
                <th className="grid-rownum" />
                {table.getHeaderGroups()[0].headers.map((header) => (
                  <th key={header.id} style={{ width: header.getSize() }}>
                    <input
                      type="text"
                      className="grid-filter"
                      placeholder="filter…"
                      value={(header.column.getFilterValue() as string) ?? ""}
                      onChange={(e) => header.column.setFilterValue(e.target.value)}
                    />
                  </th>
                ))}
              </tr>
            )}
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.id}>
                <td className="grid-rownum">{i + 1}</td>
                {row.getVisibleCells().map((cell) => {
                  const value = cell.getValue();
                  const isNull = value === null || value === undefined;
                  return (
                    <td
                      key={cell.id}
                      className="grid-cell mono"
                      style={{ width: cell.column.getSize() }}
                      title={isNull ? "NULL — click to expand" : `${asText(value)}\n(click to expand)`}
                      onClick={() =>
                        setExpanded({
                          column: cell.column.columnDef.header as string,
                          value,
                          row: row.original,
                          columns: result.columns,
                        })
                      }
                    >
                      {isNull ? <span className="grid-null">NULL</span> : display(value)}
                    </td>
                  );
                })}
              </tr>
            ))}
            {visibleCount === 0 && (
              <tr>
                <td className="grid-empty" colSpan={result.columns.length + 1}>
                  No rows match the column filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {filtered && (
        <div className="grid-footnote">
          Showing {visibleCount.toLocaleString()} of {result.row_count.toLocaleString()} rows (filtered).
        </div>
      )}

      {expanded && (
        <Modal
          title={`Cell · ${expanded.column}`}
          onClose={() => setExpanded(null)}
          wide
          footer={
            <>
              <button className="ghost small" onClick={() => copy(asText(expanded.value))}>
                <Icon name="copy" size={12} /> Copy value
              </button>
              <button
                className="small"
                onClick={() =>
                  copy(expanded.columns.map((c, i) => `${c}\t${asText(expanded.row[i])}`).join("\n"))
                }
              >
                <Icon name="copy" size={12} /> Copy row
              </button>
            </>
          }
        >
          {expanded.value === null || expanded.value === undefined ? (
            <div className="grid-null" style={{ fontSize: 14 }}>NULL</div>
          ) : (
            <pre className="result" style={{ maxHeight: "55vh", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {typeof expanded.value === "object"
                ? JSON.stringify(expanded.value, null, 2)
                : String(expanded.value)}
            </pre>
          )}
        </Modal>
      )}
    </>
  );
}
