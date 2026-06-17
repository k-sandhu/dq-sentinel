import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import * as dagre from "dagre";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import type {
  ColumnLineageNode,
  LineageEdge,
  LineageGraph as LineageGraphData,
  LineageHealth,
  LineageNode,
} from "../api/types";
import { lineageDestLabel, lineageNodeHref } from "../lib/lineageNav";
import { EmptyState, Icon, StatusPill } from "./ui";

type Granularity = "table" | "column";
type FocusMode = "none" | "upstream" | "downstream" | "both";
type HealthFilter = "all" | LineageHealth | "attention";

interface TableFlowData extends Record<string, unknown> {
  kind: "table";
  node: LineageNode;
  label: string;
  meta: string;
  dim: boolean;
  highlight: boolean;
}

interface ColumnFlowData extends Record<string, unknown> {
  kind: "column";
  column: ColumnLineageNode;
  table: LineageNode;
  label: string;
  meta: string;
  dim: boolean;
  highlight: boolean;
}

type FlowData = TableFlowData | ColumnFlowData;
type FlowNode = Node<FlowData>;

const TABLE_W = 224;
const TABLE_H = 76;
const COLUMN_W = 250;
const COLUMN_H = 62;

function tableLabel(node: LineageNode): string {
  return node.schema_name ? `${node.schema_name}.${node.table_name}` : node.table_name;
}

function shortLabel(value: string, max = 42): string {
  return value.length > max ? `${value.slice(0, max - 1)}...` : value;
}

function tableForColumnId(id: string): string {
  const parts = id.split(".");
  return parts.length > 2 ? parts.slice(0, -1).join(".") : parts[0];
}

function buildAdjacency(edges: { source: string; target: string }[]) {
  const upstream = new Map<string, Set<string>>();
  const downstream = new Map<string, Set<string>>();
  for (const edge of edges) {
    if (!downstream.has(edge.source)) downstream.set(edge.source, new Set());
    if (!upstream.has(edge.target)) upstream.set(edge.target, new Set());
    downstream.get(edge.source)!.add(edge.target);
    upstream.get(edge.target)!.add(edge.source);
  }
  return { upstream, downstream };
}

function walk(start: string, adjacency: Map<string, Set<string>>, maxDepth = 99): Set<string> {
  const seen = new Set<string>([start]);
  let frontier = new Set<string>([start]);
  for (let depth = 0; depth < maxDepth; depth += 1) {
    const next = new Set<string>();
    for (const id of frontier) for (const nbr of adjacency.get(id) ?? []) if (!seen.has(nbr)) next.add(nbr);
    if (next.size === 0) break;
    for (const id of next) seen.add(id);
    frontier = next;
  }
  return seen;
}

function layout(nodes: FlowNode[], edges: Edge[]): FlowNode[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 46, ranksep: 104, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const node of nodes) {
    const width = node.data.kind === "table" ? TABLE_W : COLUMN_W;
    const height = node.data.kind === "table" ? TABLE_H : COLUMN_H;
    g.setNode(node.id, { width, height });
  }
  for (const edge of edges) g.setEdge(edge.source, edge.target);
  dagre.layout(g);
  return nodes.map((node) => {
    const pos = g.node(node.id);
    const width = node.data.kind === "table" ? TABLE_W : COLUMN_W;
    const height = node.data.kind === "table" ? TABLE_H : COLUMN_H;
    return { ...node, position: { x: pos.x - width / 2, y: pos.y - height / 2 } };
  });
}

function TableNode({ data }: NodeProps<FlowNode>) {
  if (data.kind !== "table") return null;
  const node = data.node;
  const counts: string[] = [];
  if (node.failing_checks) counts.push(`${node.failing_checks} failing`);
  if (node.open_exceptions) counts.push(`${node.open_exceptions} open`);
  return (
    <div className={`lf-node table ${node.health} ${data.dim ? "dim" : ""} ${data.highlight ? "hit" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="lf-node-top">
        <span className="lf-node-title">{shortLabel(data.label, 32)}</span>
        <StatusPill value={node.health} />
      </div>
      <div className="lf-node-meta">{shortLabel(counts.length ? counts.join(" · ") : data.meta, 42)}</div>
      <div className="lf-node-foot">
        <span>{node.kind}</span>
        {node.owner && <span>{shortLabel(node.owner, 20)}</span>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function ColumnNode({ data }: NodeProps<FlowNode>) {
  if (data.kind !== "column") return null;
  const table = data.table;
  return (
    <div className={`lf-node column ${table.health} ${data.dim ? "dim" : ""} ${data.highlight ? "hit" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="lf-node-top">
        <span className="lf-node-title">{shortLabel(data.column.column, 34)}</span>
        <span className="badge kind">{data.column.dtype || "unknown"}</span>
      </div>
      <div className="lf-node-meta">{shortLabel(tableLabel(table), 42)}</div>
      <div className="lf-node-foot">
        <span>{data.column.nullable ? "nullable" : "required"}</span>
        <StatusPill value={table.health} />
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { tableNode: TableNode, columnNode: ColumnNode };

function edgeTone(kind?: string) {
  if (kind === "aggregate") return "var(--purple)";
  if (kind === "derived") return "var(--brand-dark)";
  if (kind === "unresolved") return "var(--warn-strong)";
  return "var(--border-light)";
}

function toFlow(
  graph: LineageGraphData,
  granularity: Granularity,
  selectedId: string | null,
  focusMode: FocusMode,
  healthFilter: HealthFilter,
  schemaFilter: string,
  search: string,
) {
  const rawEdges =
    granularity === "column"
      ? graph.column_edges.map((e) => ({ ...e, sourceTable: tableForColumnId(e.source), targetTable: tableForColumnId(e.target) }))
      : graph.edges.map((e: LineageEdge) => ({ ...e, kind: "direct", expression: null }));
  const idsFromEdges = new Set<string>();
  for (const edge of rawEdges) {
    idsFromEdges.add(edge.source);
    idsFromEdges.add(edge.target);
  }
  const adjacency = buildAdjacency(rawEdges);
  const focusIds = selectedId
    ? focusMode === "upstream"
      ? walk(selectedId, adjacency.upstream)
      : focusMode === "downstream"
        ? walk(selectedId, adjacency.downstream)
        : focusMode === "both"
          ? new Set([...walk(selectedId, adjacency.upstream), ...walk(selectedId, adjacency.downstream)])
          : null
    : null;

  const q = search.trim().toLowerCase();
  const searchMatches = new Set<string>();
  const nodes: FlowNode[] = [];

  if (granularity === "table") {
    for (const node of graph.nodes) {
      if (schemaFilter !== "all" && (node.schema_name || "(default)") !== schemaFilter) continue;
      if (healthFilter === "attention" && node.health !== "fail" && node.health !== "warn") continue;
      if (healthFilter !== "all" && healthFilter !== "attention" && node.health !== healthFilter) continue;
      const label = tableLabel(node);
      const match = q !== "" && `${label} ${node.owner} ${node.importance}`.toLowerCase().includes(q);
      if (match) searchMatches.add(node.id);
      nodes.push({
        id: node.id,
        type: "tableNode",
        position: { x: 0, y: 0 },
        data: {
          kind: "table",
          node,
          label,
          meta: node.schema_name || (node.dataset_id === null ? "external" : "registered"),
          dim: !!focusIds && !focusIds.has(node.id),
          highlight: selectedId === node.id || match,
        },
      });
    }
  } else {
    for (const table of graph.nodes) {
      if (schemaFilter !== "all" && (table.schema_name || "(default)") !== schemaFilter) continue;
      if (healthFilter === "attention" && table.health !== "fail" && table.health !== "warn") continue;
      if (healthFilter !== "all" && healthFilter !== "attention" && table.health !== healthFilter) continue;
      for (const column of table.columns ?? []) {
        if (!idsFromEdges.has(column.id) && q === "") continue;
        const label = `${tableLabel(table)}.${column.column}`;
        const match = q !== "" && label.toLowerCase().includes(q);
        if (match) searchMatches.add(column.id);
        nodes.push({
          id: column.id,
          type: "columnNode",
          position: { x: 0, y: 0 },
          data: {
            kind: "column",
            column,
            table,
            label,
            meta: column.dtype || "",
            dim: !!focusIds && !focusIds.has(column.id),
            highlight: selectedId === column.id || match,
          },
        });
      }
    }
  }

  const nodeIds = new Set(nodes.map((n) => n.id));
  const selectedPath = selectedId && focusIds ? focusIds : searchMatches;
  const edges: Edge[] = rawEdges
    .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map((edge, index) => {
      const active = selectedPath.has(edge.source) && selectedPath.has(edge.target);
      const dim = !!focusIds && (!focusIds.has(edge.source) || !focusIds.has(edge.target));
      return {
        id: `${edge.source}->${edge.target}:${edge.kind ?? "direct"}:${index}`,
        source: edge.source,
        target: edge.target,
        animated: active,
        markerEnd: { type: MarkerType.ArrowClosed, color: active ? "var(--brand)" : edgeTone(edge.kind) },
        style: {
          stroke: active ? "var(--brand)" : edgeTone(edge.kind),
          strokeWidth: active ? 2.4 : 1.6,
          opacity: dim ? 0.18 : 1,
        },
        label: granularity === "column" && edge.kind !== "direct" ? edge.kind : undefined,
        data: edge,
      };
    });

  return { nodes: layout(nodes, edges), edges, searchMatches, adjacency };
}

function LineageCanvas({
  graph,
  currentId,
  emptyHint,
  granularity = "table",
  onGranularityChange,
  depth,
  onDepthChange,
}: {
  graph: LineageGraphData;
  currentId?: string;
  emptyHint?: string;
  granularity?: Granularity;
  onGranularityChange?: (granularity: Granularity) => void;
  depth?: number;
  onDepthChange?: (depth: number) => void;
}) {
  const navigate = useNavigate();
  const reactFlow = useReactFlow();
  const [selectedId, setSelectedId] = useState<string | null>(currentId ?? null);
  const [focusMode, setFocusMode] = useState<FocusMode>("none");
  const [healthFilter, setHealthFilter] = useState<HealthFilter>("all");
  const [schemaFilter, setSchemaFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => setSelectedId(currentId ?? null), [currentId]);

  const schemas = useMemo(() => {
    const values = new Set(graph.nodes.map((n) => n.schema_name || "(default)"));
    return ["all", ...Array.from(values).sort()];
  }, [graph.nodes]);

  const { nodes, edges, searchMatches, adjacency } = useMemo(
    () => toFlow(graph, granularity, selectedId, focusMode, healthFilter, schemaFilter, search),
    [graph, granularity, selectedId, focusMode, healthFilter, schemaFilter, search],
  );

  const selectedNode = selectedId ? nodes.find((n) => n.id === selectedId) : undefined;
  const downstream = selectedId ? walk(selectedId, adjacency.downstream) : new Set<string>();
  const upstream = selectedId ? walk(selectedId, adjacency.upstream) : new Set<string>();
  downstream.delete(selectedId ?? "");
  upstream.delete(selectedId ?? "");

  useEffect(() => {
    window.setTimeout(() => reactFlow.fitView({ padding: 0.18, duration: 200 }), 0);
  }, [granularity, graph, healthFilter, schemaFilter, reactFlow]);

  if (graph.nodes.length === 0) {
    return (
      <EmptyState
        title="No lineage to draw"
        hint={emptyHint ?? "No tables or view-derived relationships were found here."}
      />
    );
  }

  const notes: string[] = [];
  if (graph.truncated) notes.push("graph truncated at 300 tables or 5,000 column edges");
  if (graph.parse_errors > 0) notes.push(`${graph.parse_errors} view definition${graph.parse_errors === 1 ? "" : "s"} could not be parsed`);
  if (graph.qualify_errors > 0) notes.push(`${graph.qualify_errors} column lineage projection${graph.qualify_errors === 1 ? "" : "s"} could not be resolved`);

  const jumpToSearch = () => {
    const first = Array.from(searchMatches)[0];
    if (!first) return;
    setSelectedId(first);
    const node = nodes.find((n) => n.id === first);
    if (node) reactFlow.setCenter(node.position.x + 110, node.position.y + 40, { zoom: 1.1, duration: 250 });
  };

  // Select a node and bring it into view — shared by the search jump and the
  // "needs attention" list so a click always lands you on the node + its panel.
  // The attention list is drawn from the full graph, so if an active filter is
  // hiding the target we clear it first; otherwise selecting it would render
  // nothing (the node isn't in the filtered set) and the click would feel dead.
  const pickNode = (id: string) => {
    if (!nodes.some((n) => n.id === id)) {
      setHealthFilter("all");
      setSchemaFilter("all");
    }
    setSelectedId(id);
    const node = nodes.find((n) => n.id === id);
    if (node) reactFlow.setCenter(node.position.x + 110, node.position.y + 38, { zoom: 1.05, duration: 250 });
  };

  // Failing/warning tables, worst first — the side rail's default content when
  // nothing is selected (replaces the old separate "Needs attention" page card).
  const attention = graph.nodes
    .filter((n) => n.health === "fail" || n.health === "warn")
    .sort((a, b) => {
      if (a.health !== b.health) return a.health === "fail" ? -1 : 1;
      return b.failing_checks - a.failing_checks || b.open_exceptions - a.open_exceptions;
    });

  return (
    <div className={`lf-root${fullscreen ? " fullscreen" : ""}`}>
      <div className="lf-toolbar">
        <div className="lf-search">
          <Icon name="search" size={13} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && jumpToSearch()}
            placeholder={granularity === "column" ? "Search table.column" : "Search table, owner, importance"}
          />
          <button className="small" onClick={jumpToSearch} disabled={searchMatches.size === 0}>
            Jump
          </button>
        </div>
        <select value={healthFilter} onChange={(e) => setHealthFilter(e.target.value as HealthFilter)} aria-label="Health filter">
          <option value="all">all health</option>
          <option value="attention">fail or warn</option>
          <option value="fail">fail</option>
          <option value="warn">warn</option>
          <option value="pass">pass</option>
          <option value="unknown">unknown</option>
        </select>
        <select value={schemaFilter} onChange={(e) => setSchemaFilter(e.target.value)} aria-label="Schema filter">
          {schemas.map((s) => <option key={s} value={s}>{s === "all" ? "all schemas" : s}</option>)}
        </select>
        <select value={focusMode} onChange={(e) => setFocusMode(e.target.value as FocusMode)} aria-label="Focus mode">
          <option value="none">no focus</option>
          <option value="upstream">upstream</option>
          <option value="downstream">downstream</option>
          <option value="both">up + down</option>
        </select>
        {onDepthChange && (
          <select value={depth ?? 2} onChange={(e) => onDepthChange(Number(e.target.value))} aria-label="Depth">
            {[1, 2, 3, 4, 5].map((d) => <option key={d} value={d}>{d} hop{d === 1 ? "" : "s"}</option>)}
          </select>
        )}
        {onGranularityChange && (
          <div className="chip-row">
            {(["table", "column"] as const).map((g) => (
              <button key={g} className={`filter-chip${granularity === g ? " on" : ""}`} onClick={() => onGranularityChange(g)}>
                {g}
              </button>
            ))}
          </div>
        )}
        <button className="small" onClick={() => reactFlow.fitView({ padding: 0.18, duration: 250 })}>
          Fit
        </button>
        <button className="small" onClick={() => setFullscreen((v) => !v)}>
          {fullscreen ? "Exit full" : "Full"}
        </button>
      </div>

      <div className="lf-body">
        <div className="lf-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            minZoom={0.12}
            maxZoom={1.8}
            onNodeClick={(_, node) => setSelectedId(node.id)}
            onNodeDoubleClick={(_, node) => {
              const table = node.data.kind === "table" ? node.data.node : node.data.table;
              const href = lineageNodeHref(table);
              if (href) navigate(href);
            }}
          >
            <Background gap={18} size={1} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={(n) => {
              const d = n.data as FlowData;
              const health = d.kind === "table" ? d.node.health : d.table.health;
              return health === "fail" ? "#ed6e6e" : health === "warn" ? "#e8a541" : health === "pass" ? "#84bb4c" : "#b8c0c8";
            }} />
          </ReactFlow>
        </div>
        {selectedNode ? (
          <DetailPanel
            node={selectedNode}
            upstreamCount={upstream.size}
            downstreamCount={downstream.size}
            onClose={() => setSelectedId(null)}
          />
        ) : (
          <OverviewPanel graph={graph} attention={attention} onPick={pickNode} />
        )}
      </div>

      <div className="legend-row lf-legend">
        <span><span className="swatch ok" /> pass</span>
        <span><span className="swatch warn" /> warn</span>
        <span><span className="swatch fail" /> fail</span>
        <span><span className="swatch ext" /> unknown / external</span>
        <span style={{ marginLeft: "auto" }}>
          {nodes.length.toLocaleString()} {granularity === "column" ? "columns" : "tables"} · {edges.length.toLocaleString()} edges
        </span>
      </div>
      {notes.length > 0 && <div className="lineage-note">{notes.join(" · ")}</div>}
    </div>
  );
}

function DetailPanel({
  node,
  upstreamCount,
  downstreamCount,
  onClose,
}: {
  node: FlowNode;
  upstreamCount: number;
  downstreamCount: number;
  onClose: () => void;
}) {
  const data = node.data;
  const table = data.kind === "table" ? data.node : data.table;
  const href = lineageNodeHref(table);
  const label = data.kind === "table" ? tableLabel(table) : `${tableLabel(table)}.${data.column.column}`;
  return (
    <aside className="lf-panel">
      <div className="lf-panel-head">
        <div>
          <h3>{shortLabel(label, 54)}</h3>
          <div className="muted">{data.kind === "table" ? table.kind : data.column.dtype || "column"}</div>
        </div>
        <button className="ghost small" onClick={onClose} aria-label="Close lineage details"><Icon name="x" size={14} /></button>
      </div>
      <div className="lf-panel-body">
        <div className="lf-kpis">
          <div><strong>{downstreamCount}</strong><span>downstream</span></div>
          <div><strong>{upstreamCount}</strong><span>upstream</span></div>
        </div>
        <div className="lf-panel-section">
          <div className="lf-panel-label">Health</div>
          <div className="lf-panel-badges">
            <StatusPill value={table.health} />
            <span>{table.failing_checks} failing checks</span>
            <span>{table.open_exceptions} open exceptions</span>
          </div>
        </div>
        <div className="lf-panel-section">
          <div className="lf-panel-label">Ownership</div>
          <div>{table.owner || "No owner recorded"}</div>
          <div className="muted">{table.importance ? `Importance: ${table.importance}` : "No importance recorded"}</div>
        </div>
        {data.kind === "column" && (
          <div className="lf-panel-section">
            <div className="lf-panel-label">Column</div>
            <div>{data.column.column}</div>
            <div className="muted">{data.column.nullable ? "Nullable" : "Required"}</div>
          </div>
        )}
        <div className="lf-panel-section">
          <div className="lf-panel-label">Actions</div>
          {href ? (
            <Link className="btn primary lf-open" to={href}>
              Open {lineageDestLabel(table)}
            </Link>
          ) : (
            <div className="muted">External / unregistered table — register it as a dataset to open it.</div>
          )}
          {table.dataset_id !== null && (
            <div className="lf-actions">
              <Link className="btn small" to={`/datasets/${table.dataset_id}/lineage`}>Lineage</Link>
              <Link className="btn small" to={`/datasets/${table.dataset_id}/exceptions`}>Exceptions</Link>
              <Link className="btn small" to={`/datasets/${table.dataset_id}/rca`}>RCA</Link>
              <Link className="btn small" to={`/workbench?dataset_id=${table.dataset_id}`}>Workbench</Link>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

// Default rail content when no node is selected: a quick read on the graph plus a
// worst-first "needs attention" jump list. Clicking an item selects + centers the
// node (so its detail panel opens), folding the old standalone page rail in here.
function OverviewPanel({
  graph,
  attention,
  onPick,
}: {
  graph: LineageGraphData;
  attention: LineageNode[];
  onPick: (id: string) => void;
}) {
  return (
    <aside className="lf-panel">
      <div className="lf-panel-head">
        <div>
          <h3>Overview</h3>
          <div className="muted">Click a node to inspect · double-click to open</div>
        </div>
      </div>
      <div className="lf-panel-body">
        <div className="lf-kpis">
          <div><strong>{graph.nodes.length.toLocaleString()}</strong><span>tables</span></div>
          <div><strong>{attention.length.toLocaleString()}</strong><span>need attention</span></div>
        </div>
        <div className="lf-panel-section">
          <div className="lf-panel-label">Needs attention</div>
          {attention.length === 0 ? (
            <div className="muted">All clear — nothing failing or warning in this graph.</div>
          ) : (
            <div className="dense-list">
              {attention.slice(0, 60).map((n) => (
                <div key={n.id} className="dense-item clickable" onClick={() => onPick(n.id)}>
                  <div className="lf-att-title">
                    <span className="lf-att-name">{tableLabel(n)}</span>
                    <StatusPill value={n.health} />
                  </div>
                  <div className="meta">
                    {n.failing_checks} failing · {n.open_exceptions} open
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

export default function LineageGraph(props: {
  graph: LineageGraphData;
  currentId?: string;
  emptyHint?: string;
  granularity?: Granularity;
  onGranularityChange?: (granularity: Granularity) => void;
  depth?: number;
  onDepthChange?: (depth: number) => void;
}) {
  return (
    <ReactFlowProvider>
      <LineageCanvas {...props} />
    </ReactFlowProvider>
  );
}
