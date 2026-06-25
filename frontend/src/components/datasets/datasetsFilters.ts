// Pure filter/sort/URL helpers for the Datasets surface (D4 / FE-2). Kept free of
// React so the brain of the page — search, the scorecard rollup filters, the health
// filter, and the favorites-first sort — is unit-testable in isolation.

import type { Dataset } from "../../api/types";

export const HEALTH_FILTERS = ["all", "fail", "warn", "pass", "unknown"] as const;
export type HealthFilter = (typeof HEALTH_FILTERS)[number];

export type RollupFilterKey = "domain" | "team";
export interface ActiveRollupFilter {
  key: RollupFilterKey;
  label: string;
  value: string;
}

/** Case-insensitive, trimmed equality. A `null` filter means "no filter" (match
 *  all); an empty-string filter matches empty/unassigned values ("Unassigned"). */
export function matchesRollupFilter(
  value: string | null | undefined,
  filter: string | null,
): boolean {
  if (filter === null) return true;
  return (value ?? "").trim().toLowerCase() === filter.trim().toLowerCase();
}

/** Read the scorecard rollup filters (domain/team) from the URL. A present-but-empty
 *  param is "Unassigned" and is distinct from an absent param (no filter at all),
 *  so a scorecard drill-in on the Unassigned bucket survives a reload. */
export function parseRollupFilters(sp: URLSearchParams): {
  domainFilter: string | null;
  teamFilter: string | null;
  activeRollupFilters: ActiveRollupFilter[];
} {
  const domainFilter = sp.has("domain") ? (sp.get("domain") ?? "") : null;
  const teamFilter = sp.has("team") ? (sp.get("team") ?? "") : null;
  const activeRollupFilters = [
    domainFilter !== null
      ? { key: "domain" as const, label: "Domain", value: domainFilter || "Unassigned" }
      : null,
    teamFilter !== null
      ? { key: "team" as const, label: "Team", value: teamFilter || "Unassigned" }
      : null,
  ].filter((item): item is ActiveRollupFilter => item !== null);
  return { domainFilter, teamFilter, activeRollupFilters };
}

export interface DatasetFilterState {
  search: string;
  healthFilter: HealthFilter;
  favSet: Set<number>;
  domainFilter: string | null;
  teamFilter: string | null;
}

/** Apply the rollup, search, and health filters, then sort: favorites float to the
 *  top and, within each band, the most open exceptions first. Pure and stable. */
export function filterAndSortDatasets(
  raw: Dataset[] | undefined,
  f: DatasetFilterState,
): Dataset[] {
  let list = raw ?? [];
  if (f.domainFilter !== null) list = list.filter((d) => matchesRollupFilter(d.domain, f.domainFilter));
  if (f.teamFilter !== null) list = list.filter((d) => matchesRollupFilter(d.team, f.teamFilter));
  if (f.search) {
    const needle = f.search.toLowerCase();
    list = list.filter(
      (d) =>
        d.table_name.toLowerCase().includes(needle) ||
        d.connection_name.toLowerCase().includes(needle) ||
        (d.owner ?? "").toLowerCase().includes(needle) ||
        (d.domain ?? "").toLowerCase().includes(needle) ||
        (d.team ?? "").toLowerCase().includes(needle),
    );
  }
  if (f.healthFilter !== "all") {
    list = list.filter((d) => (d.health ?? "unknown") === f.healthFilter);
  }
  return [...list].sort((a, b) => {
    const favDelta = Number(f.favSet.has(b.id)) - Number(f.favSet.has(a.id));
    return favDelta !== 0 ? favDelta : b.open_exceptions - a.open_exceptions;
  });
}

/** Recently-viewed datasets resolved against the live list: drop ids no longer
 *  present, preserve most-recent-first order. */
export function resolveRecentDatasets(
  raw: Dataset[] | undefined,
  recents: { id: number }[],
): Dataset[] {
  if (!raw) return [];
  const byId = new Map(raw.map((d) => [d.id, d]));
  return recents
    .map((r) => byId.get(r.id))
    .filter((d): d is Dataset => d !== undefined);
}
