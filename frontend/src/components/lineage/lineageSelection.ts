import type { Connection } from "../../api/types";

/** Resolve the active lineage connection from the `?connection=` URL param.
 *
 *  - `fromParam` is the explicitly-selected id (a finite numeric param), else null.
 *  - `connectionId` falls back to the first connection so the page renders something
 *    on first load; the page writes that default back into the URL (shareable link).
 *
 *  Kept pure so the param/default precedence is unit-testable without a router. */
export function resolveLineageConnection(
  rawParam: string | null,
  connections: Connection[] | undefined,
): { fromParam: number | null; connectionId: number | null } {
  const fromParam = rawParam && Number.isFinite(Number(rawParam)) ? Number(rawParam) : null;
  return { fromParam, connectionId: fromParam ?? connections?.[0]?.id ?? null };
}
