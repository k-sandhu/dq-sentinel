import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PanelViz } from "../api/types";
import { fmtValue } from "../lib/format";

// CSS variables so panels follow the active theme (light/dark).
const PALETTE = [
  "var(--brand)",
  "var(--ok)",
  "var(--yellow)",
  "var(--danger)",
  "var(--purple)",
  "var(--warn)",
  "var(--teal)",
  "var(--slate)",
];

const AXIS = { fontSize: 11, fill: "var(--text-light)" };
const TOOLTIP_STYLE = {
  fontSize: 12,
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--card)",
  color: "var(--text-dark)",
};

function toObjects(columns: string[], rows: unknown[][]): Record<string, unknown>[] {
  return rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]])));
}

/**
 * Normalize an x-axis cell to something orderable: epoch-ms for dates, a number
 * for numerics, the trimmed text otherwise. `null` means "not comparable".
 *
 * Numeric-looking strings are compared as numbers on purpose — `new Date("5")`
 * happily yields May 2001, which would make bucket labels sort as dates.
 */
function xSortValue(v: unknown): number | string | null {
  if (v == null) return null;
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : v.getTime();
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v !== "string") return null;
  const s = v.trim();
  if (s === "") return null;
  if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s);
  if (/^\d{4}-\d{2}(-\d{2})?([T ].*)?$/.test(s)) {
    const t = Date.parse(s);
    if (Number.isFinite(t)) return t;
  }
  return s;
}

/**
 * Is this series ordered newest/highest-first?
 *
 * Panels are authored by hand, by saved queries and by the LLM, so both
 * `ORDER BY day DESC` ("latest first", the dashboard habit) and `ORDER BY day ASC`
 * arrive here. The old code reversed line/area data unconditionally, which meant
 * every ASC series rendered right-to-left and silently inverted its trend — an
 * improving pass-rate read as degrading. Heuristic instead: compare the FIRST and
 * LAST x value and only reverse when the series really does run backwards. It is
 * a heuristic, not a sort — a genuinely unordered x column is left untouched, and
 * non-comparable / single-row data never reverses.
 */
function isDescendingByX(data: Record<string, unknown>[], x: string): boolean {
  if (data.length < 2) return false;
  const first = xSortValue(data[0][x]);
  const last = xSortValue(data[data.length - 1][x]);
  if (first === null || last === null) return false;
  if (typeof first === "number" && typeof last === "number") return first > last;
  if (typeof first === "string" && typeof last === "string") return first.localeCompare(last) > 0;
  return false; // mixed types — don't guess
}

export default function PanelChart({
  columns,
  rows,
  viz,
  height = 200,
}: {
  columns: string[];
  rows: unknown[][];
  viz: PanelViz;
  height?: number;
}) {
  if (!columns.length) return <div className="empty" style={{ padding: 18 }}>No data</div>;

  const x = viz.x && columns.includes(viz.x) ? viz.x : columns[0];
  const y = viz.y && columns.includes(viz.y) ? viz.y : columns[columns.length - 1];

  if (viz.type === "number") {
    const idx = viz.y && columns.includes(viz.y) ? columns.indexOf(viz.y) : 0;
    const value = rows[0]?.[idx];
    return (
      <div style={{ fontSize: 34, fontWeight: 800, color: "var(--text-dark)", padding: "12px 4px" }}>
        {fmtValue(value)}
      </div>
    );
  }

  if (viz.type === "table" || rows.length === 0) {
    return (
      <div className="table-wrap" style={{ maxHeight: height + 60, overflowY: "auto" }}>
        <table className="data">
          <thead>
            <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.slice(0, 50).map((r, i) => (
              <tr key={i}>
                {r.map((v, j) => (
                  <td key={j} className="mono" style={{ whiteSpace: "nowrap", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {fmtValue(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  // Line/area charts read left-to-right, so a DESC time series must be flipped —
  // but only when it actually is DESC (see `isDescendingByX`). Bar/pie keep the
  // SQL's own order: their x is usually a category ranked by y, where reversing
  // would just scramble a deliberate "top N" ordering.
  const sqlOrder = toObjects(columns, rows);
  const data = isDescendingByX(sqlOrder, x) ? [...sqlOrder].reverse() : sqlOrder;

  if (viz.type === "pie") {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie data={sqlOrder.slice(0, 8)} dataKey={y} nameKey={x} outerRadius={Math.min(80, height / 2 - 10)} label={(e) => String(e[x]).slice(0, 14)} labelLine={false} fontSize={11}>
            {sqlOrder.slice(0, 8).map((_e, i) => (
              <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
            ))}
          </Pie>
          <Tooltip contentStyle={TOOLTIP_STYLE} />
        </PieChart>
      </ResponsiveContainer>
    );
  }

  const common = (
    <>
      <CartesianGrid stroke="var(--border-light)" vertical={false} />
      <XAxis dataKey={x} tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--border)" }} tickFormatter={(v) => String(v).slice(0, 12)} />
      <YAxis tick={AXIS} tickLine={false} axisLine={false} width={52} />
      <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "var(--hover)" }} />
    </>
  );

  if (viz.type === "line") {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <LineChart accessibilityLayer data={data} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
          {common}
          <Line type="monotone" dataKey={y} stroke="var(--brand)" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    );
  }
  if (viz.type === "area") {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart accessibilityLayer data={data} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
          {common}
          <Area type="monotone" dataKey={y} stroke="var(--brand)" fill="var(--brand-light)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart accessibilityLayer data={sqlOrder} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
        {common}
        <Bar dataKey={y} fill="var(--brand)" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
