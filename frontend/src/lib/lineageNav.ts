// One default destination per lineage object (issue #82 / BF-8). Every surface
// that lets you click a table in lineage — the SVG graph, the "needs attention"
// rail, the relationships table — routes through this so the same intent always
// lands in the same place: failing/warning tables open to their exceptions (what
// is wrong right now), everything else opens to the profile (what the table is).
import type { LineageNode } from "../api/types";

export function lineageNodeHref(node: Pick<LineageNode, "dataset_id" | "health">): string | null {
  if (node.dataset_id === null) return null; // external / unregistered — nothing to open
  const base = `/datasets/${node.dataset_id}`;
  return node.health === "fail" || node.health === "warn" ? `${base}/exceptions` : base;
}

// Label for the default destination above, for tooltips / legends.
export function lineageDestLabel(node: Pick<LineageNode, "health">): string {
  return node.health === "fail" || node.health === "warn" ? "exceptions" : "profile";
}
