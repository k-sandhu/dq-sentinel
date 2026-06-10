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

const PALETTE = ["#509ee3", "#84bb4c", "#f7c844", "#ed6e6e", "#a989c5", "#f2a86f", "#69c8c8", "#b8c0c8"];

const AXIS = { fontSize: 11, fill: "#949aab" };
const TOOLTIP_STYLE = { fontSize: 12, borderRadius: 8, border: "1px solid #e3e7e9" };

function toObjects(columns: string[], rows: unknown[][]): Record<string, unknown>[] {
  return rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]])));
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

  // time series often come back DESC — render ascending
  const data = toObjects(columns, rows).slice().reverse();
  const dataAsc = viz.type === "bar" || viz.type === "pie" ? toObjects(columns, rows) : data;

  if (viz.type === "pie") {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie data={dataAsc.slice(0, 8)} dataKey={y} nameKey={x} outerRadius={Math.min(80, height / 2 - 10)} label={(e) => String(e[x]).slice(0, 14)} labelLine={false} fontSize={11}>
            {dataAsc.slice(0, 8).map((_e, i) => (
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
      <CartesianGrid stroke="#eef0f2" vertical={false} />
      <XAxis dataKey={x} tick={AXIS} tickLine={false} axisLine={{ stroke: "#e3e7e9" }} tickFormatter={(v) => String(v).slice(0, 12)} />
      <YAxis tick={AXIS} tickLine={false} axisLine={false} width={52} />
      <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f5f9fd" }} />
    </>
  );

  if (viz.type === "line") {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
          {common}
          <Line type="monotone" dataKey={y} stroke="#509ee3" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    );
  }
  if (viz.type === "area") {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
          {common}
          <Area type="monotone" dataKey={y} stroke="#509ee3" fill="#cbe2f7" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={dataAsc} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
        {common}
        <Bar dataKey={y} fill="#509ee3" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
