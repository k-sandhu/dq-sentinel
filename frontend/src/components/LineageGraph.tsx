// Dependency-free SVG renderer for table-level lineage DAGs (issue #51).
// Layered left-to-right layout: longest-path layering via Kahn topological
// ordering, then a single barycenter refinement pass per layer. Pan = native
// scroll on the .lineage-canvas wrapper. All colors come from CSS variables so
// dark mode works automatically.
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import type { LineageGraph as LineageGraphData, LineageNode } from "../api/types";
import { EmptyState } from "./ui";

const NODE_W = 190;
const NODE_H = 56;
const COL_PITCH = 250;
const ROW_PITCH = 80;
const PAD = 24;

interface PlacedNode {
  node: LineageNode;
  x: number;
  y: number;
}

interface LayoutResult {
  placed: Map<string, PlacedNode>;
  edges: { source: string; target: string }[];
  width: number;
  height: number;
}

function layout(graph: LineageGraphData): LayoutResult {
  const ids = new Set(graph.nodes.map((n) => n.id));
  // Drop self-loops and edges pointing at nodes we don't have (defensive).
  const edges = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target) && e.source !== e.target);

  const out = new Map<string, string[]>();
  const inDegree = new Map<string, number>();
  for (const n of graph.nodes) {
    out.set(n.id, []);
    inDegree.set(n.id, 0);
  }
  for (const e of edges) {
    out.get(e.source)!.push(e.target);
    inDegree.set(e.target, (inDegree.get(e.target) ?? 0) + 1);
  }

  // Kahn topological ordering; layer = longest path from any source node.
  const layer = new Map<string, number>();
  const queue: string[] = [];
  for (const n of graph.nodes) {
    if ((inDegree.get(n.id) ?? 0) === 0) {
      queue.push(n.id);
      layer.set(n.id, 0);
    }
  }
  for (let qi = 0; qi < queue.length; qi++) {
    const id = queue[qi];
    const base = layer.get(id) ?? 0;
    for (const t of out.get(id) ?? []) {
      layer.set(t, Math.max(layer.get(t) ?? 0, base + 1));
      const left = (inDegree.get(t) ?? 1) - 1;
      inDegree.set(t, left);
      if (left === 0) queue.push(t);
    }
  }
  // Anything never dequeued sits on a cycle — park it one column past the rest.
  let maxLayer = 0;
  for (const v of layer.values()) maxLayer = Math.max(maxLayer, v);
  const leftovers = graph.nodes.filter((n) => !layer.has(n.id));
  if (leftovers.length > 0) {
    maxLayer += 1;
    for (const n of leftovers) layer.set(n.id, maxLayer);
  }

  const columns: LineageNode[][] = Array.from({ length: maxLayer + 1 }, () => []);
  for (const n of graph.nodes) columns[layer.get(n.id) ?? 0].push(n);

  // Upstream neighbors per node, for barycenter ordering.
  const upstream = new Map<string, string[]>();
  for (const e of edges) {
    const list = upstream.get(e.target);
    if (list) list.push(e.source);
    else upstream.set(e.target, [e.source]);
  }

  // Single left-to-right refinement pass: order each column by the mean
  // y-index of its (already ordered) upstream neighbors.
  const rowIndex = new Map<string, number>();
  columns[0].forEach((n, i) => rowIndex.set(n.id, i));
  for (let c = 1; c < columns.length; c++) {
    const col = columns[c];
    const score = new Map<string, number>();
    col.forEach((n, i) => {
      const ups = (upstream.get(n.id) ?? []).filter((u) => (layer.get(u) ?? 0) < c);
      score.set(n.id, ups.length === 0 ? i : ups.reduce((s, u) => s + (rowIndex.get(u) ?? 0), 0) / ups.length);
    });
    col.sort((a, b) => score.get(a.id)! - score.get(b.id)! || a.id.localeCompare(b.id));
    col.forEach((n, i) => rowIndex.set(n.id, i));
  }

  const tallest = Math.max(1, ...columns.map((c) => c.length));
  const placed = new Map<string, PlacedNode>();
  columns.forEach((col, c) => {
    const offset = ((tallest - col.length) * ROW_PITCH) / 2; // center short columns
    col.forEach((n, r) => {
      placed.set(n.id, { node: n, x: PAD + c * COL_PITCH, y: PAD + offset + r * ROW_PITCH });
    });
  });
  return {
    placed,
    edges,
    width: PAD * 2 + maxLayer * COL_PITCH + NODE_W,
    height: PAD * 2 + (tallest - 1) * ROW_PITCH + NODE_H,
  };
}

function trunc(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export default function LineageGraph({
  graph,
  currentId,
  emptyHint,
}: {
  graph: LineageGraphData;
  currentId?: string;
  emptyHint?: string;
}) {
  const navigate = useNavigate();
  const [hoverId, setHoverId] = useState<string | null>(null);
  const { placed, edges, width, height } = useMemo(() => layout(graph), [graph]);

  if (graph.nodes.length === 0) {
    return (
      <EmptyState
        title="No lineage to draw"
        hint={emptyHint ?? "No tables or view-derived relationships were found here."}
      />
    );
  }

  // Hovered node + everything sharing an edge with it stays at full opacity.
  let related: Set<string> | null = null;
  if (hoverId !== null) {
    related = new Set([hoverId]);
    for (const e of edges) {
      if (e.source === hoverId) related.add(e.target);
      if (e.target === hoverId) related.add(e.source);
    }
  }

  const notes: string[] = [];
  if (graph.truncated) notes.push("graph truncated at 300 nodes");
  if (graph.parse_errors > 0) {
    notes.push(
      `${graph.parse_errors} view definition${graph.parse_errors === 1 ? "" : "s"} could not be parsed`,
    );
  }

  return (
    <div>
      <div className="lineage-canvas">
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Data lineage graph">
          {edges.map((e, i) => {
            const s = placed.get(e.source);
            const t = placed.get(e.target);
            if (!s || !t) return null;
            const x1 = s.x + NODE_W;
            const y1 = s.y + NODE_H / 2;
            const x2 = t.x;
            const y2 = t.y + NODE_H / 2;
            const dx = Math.max(36, (x2 - x1) / 2);
            const on = hoverId !== null && (e.source === hoverId || e.target === hoverId);
            const cls = on ? "lineage-edge on" : hoverId !== null ? "lineage-edge dim" : "lineage-edge";
            return (
              <path
                key={`${e.source}->${e.target}-${i}`}
                className={cls}
                d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
              />
            );
          })}
          {graph.nodes.map((n) => {
            const p = placed.get(n.id);
            if (!p) return null;
            const clickable = n.dataset_id !== null;
            const isCurrent = currentId !== undefined && n.id === currentId;
            const isView = n.kind === "view";
            const dim = related !== null && !related.has(n.id);

            const metaBits: string[] = [];
            if (n.schema_name) metaBits.push(n.schema_name);
            if (n.dataset_id === null) metaBits.push("external");
            const counts: string[] = [];
            if (n.failing_checks > 0) counts.push(`${n.failing_checks} failing`);
            if (n.open_exceptions > 0) counts.push(`${n.open_exceptions} open`);
            if (counts.length > 0) metaBits.push(counts.join(" · "));
            if (metaBits.length === 0) metaBits.push(n.kind);

            const cls = ["lineage-node", clickable ? "link" : "", isCurrent ? "current" : "", dim ? "dim" : ""]
              .filter(Boolean)
              .join(" ");
            return (
              <g
                key={n.id}
                className={cls}
                transform={`translate(${p.x}, ${p.y})`}
                onMouseEnter={() => setHoverId(n.id)}
                onMouseLeave={() => setHoverId(null)}
                onClick={clickable ? () => navigate(`/datasets/${n.dataset_id}/lineage`) : undefined}
              >
                <title>
                  {`${n.schema_name ? `${n.schema_name}.` : ""}${n.table_name} — ${n.kind}, health: ${n.health}${clickable ? "" : " (not registered as a dataset)"}`}
                </title>
                {isCurrent && <rect className="lineage-glow" x={-4} y={-4} width={NODE_W + 8} height={NODE_H + 8} rx={12} />}
                <rect className={`lineage-box ${n.health}`} width={NODE_W} height={NODE_H} rx={8} />
                <text className="lineage-title" x={12} y={23}>
                  {trunc(n.table_name, isView ? 19 : 24)}
                </text>
                <text className="lineage-meta" x={12} y={41}>
                  {trunc(metaBits.join(" · "), 30)}
                </text>
                {isView && (
                  <g transform={`translate(${NODE_W - 42}, 9)`}>
                    <rect className="lineage-badge" width={34} height={15} rx={4} />
                    <text className="lineage-badge-text" x={17} y={11} textAnchor="middle">
                      view
                    </text>
                  </g>
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <div className="legend-row">
        <span>
          <span className="swatch ok" /> pass
        </span>
        <span>
          <span className="swatch warn" /> warn
        </span>
        <span>
          <span className="swatch fail" /> fail
        </span>
        <span>
          <span className="swatch ext" /> external / no checks
        </span>
        <span style={{ marginLeft: "auto" }}>scroll to pan · click a registered table to open it</span>
      </div>
      {notes.length > 0 && <div className="lineage-note">{notes.join(" · ")}</div>}
    </div>
  );
}
