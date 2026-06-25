// Node + edge colors for the lineage graph, returned as CSS custom-property
// references (never raw hex) so the React Flow canvas — including the MiniMap and
// edge strokes — re-skins live with the active theme (D4 acceptance: the graph
// health colors read from the status tokens --ok/--warn-strong/--danger/--slate).
//
// CSS custom properties are live, so an SVG `fill="var(--danger)"` re-resolves on a
// theme switch without a React re-render.

/** Node health color. Mirrors the status tokens: fail → danger, warn → warn-strong,
 *  pass → ok; everything else (unknown / external / null) → slate. */
export function healthColor(health: string | null | undefined): string {
  if (health === "fail") return "var(--danger)";
  if (health === "warn") return "var(--warn-strong)";
  if (health === "pass") return "var(--ok)";
  return "var(--slate)";
}

/** Edge color by derivation kind. aggregate → purple, derived → brand-dark,
 *  unresolved → warn-strong, direct/unknown → slate. --slate reads clearly on both
 *  light and dark canvases (the old --border-light left direct edges near-invisible). */
export function edgeTone(kind?: string): string {
  if (kind === "aggregate") return "var(--purple)";
  if (kind === "derived") return "var(--brand-dark)";
  if (kind === "unresolved") return "var(--warn-strong)";
  return "var(--slate)";
}
