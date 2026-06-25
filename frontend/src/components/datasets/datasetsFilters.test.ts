import { describe, expect, it } from "vitest";

import type { Dataset } from "../../api/types";
import {
  filterAndSortDatasets,
  matchesRollupFilter,
  parseRollupFilters,
  resolveRecentDatasets,
} from "./datasetsFilters";

const makeDataset = (o: Partial<Dataset>): Dataset =>
  ({
    id: 1,
    connection_id: 1,
    connection_name: "warehouse",
    schema_name: "public",
    table_name: "orders",
    display_name: "orders",
    row_count: 0,
    last_profiled_at: null,
    created_at: "2026-01-01T00:00:00Z",
    active_checks: 0,
    open_exceptions: 0,
    health: "pass",
    importance: null,
    owner: null,
    domain: null,
    team: null,
    slo_target_score: null,
    slo_window_days: null,
    slo_enabled: false,
    ...o,
  }) as Dataset;

describe("matchesRollupFilter", () => {
  it("treats a null filter as match-all", () => {
    expect(matchesRollupFilter("anything", null)).toBe(true);
    expect(matchesRollupFilter(null, null)).toBe(true);
  });

  it("matches case-insensitively and trims", () => {
    expect(matchesRollupFilter("Finance", "  finance ")).toBe(true);
    expect(matchesRollupFilter("Sales", "finance")).toBe(false);
  });

  it("an empty-string filter matches empty/unassigned values", () => {
    expect(matchesRollupFilter(null, "")).toBe(true);
    expect(matchesRollupFilter("", "")).toBe(true);
    expect(matchesRollupFilter("finance", "")).toBe(false);
  });
});

describe("parseRollupFilters", () => {
  it("distinguishes an absent param (no filter) from a present-but-empty one (Unassigned)", () => {
    const none = parseRollupFilters(new URLSearchParams(""));
    expect(none.domainFilter).toBeNull();
    expect(none.activeRollupFilters).toEqual([]);

    const empty = parseRollupFilters(new URLSearchParams("domain="));
    expect(empty.domainFilter).toBe("");
    expect(empty.activeRollupFilters).toEqual([
      { key: "domain", label: "Domain", value: "Unassigned" },
    ]);
  });

  it("surfaces both domain and team chips with their values", () => {
    const { activeRollupFilters } = parseRollupFilters(
      new URLSearchParams("domain=Finance&team=Platform"),
    );
    expect(activeRollupFilters).toEqual([
      { key: "domain", label: "Domain", value: "Finance" },
      { key: "team", label: "Team", value: "Platform" },
    ]);
  });
});

describe("filterAndSortDatasets", () => {
  const a = makeDataset({ id: 1, table_name: "orders", open_exceptions: 2, health: "fail" });
  const b = makeDataset({ id: 2, table_name: "customers", open_exceptions: 9, health: "pass", domain: "Finance" });
  const c = makeDataset({ id: 3, table_name: "events", open_exceptions: 0, health: "warn", team: "Platform" });
  const base = { search: "", healthFilter: "all" as const, favSet: new Set<number>(), domainFilter: null, teamFilter: null };

  it("returns [] for undefined input", () => {
    expect(filterAndSortDatasets(undefined, base)).toEqual([]);
  });

  it("sorts by open exceptions desc, with favorites floated to the top", () => {
    const sorted = filterAndSortDatasets([a, b, c], base).map((d) => d.id);
    expect(sorted).toEqual([2, 1, 3]); // 9, 2, 0 open exceptions

    const withFav = filterAndSortDatasets([a, b, c], { ...base, favSet: new Set([3]) }).map((d) => d.id);
    expect(withFav).toEqual([3, 2, 1]); // favorite #3 first, then by open exceptions
  });

  it("filters by search across table/connection/owner/domain/team", () => {
    expect(filterAndSortDatasets([a, b, c], { ...base, search: "custom" }).map((d) => d.id)).toEqual([2]);
    expect(filterAndSortDatasets([a, b, c], { ...base, search: "finance" }).map((d) => d.id)).toEqual([2]);
    expect(filterAndSortDatasets([a, b, c], { ...base, search: "platform" }).map((d) => d.id)).toEqual([3]);
  });

  it("filters by health, treating a null health as 'unknown'", () => {
    const d = makeDataset({ id: 4, health: null });
    expect(filterAndSortDatasets([a, b, c, d], { ...base, healthFilter: "fail" }).map((x) => x.id)).toEqual([1]);
    expect(filterAndSortDatasets([a, b, c, d], { ...base, healthFilter: "unknown" }).map((x) => x.id)).toEqual([4]);
  });

  it("filters by the domain/team rollup", () => {
    expect(filterAndSortDatasets([a, b, c], { ...base, domainFilter: "Finance" }).map((d) => d.id)).toEqual([2]);
    expect(filterAndSortDatasets([a, b, c], { ...base, teamFilter: "Platform" }).map((d) => d.id)).toEqual([3]);
  });
});

describe("resolveRecentDatasets", () => {
  it("resolves ids against the live list, dropping unknowns and keeping order", () => {
    const list = [makeDataset({ id: 1 }), makeDataset({ id: 2 })];
    const out = resolveRecentDatasets(list, [{ id: 2 }, { id: 99 }, { id: 1 }]);
    expect(out.map((d) => d.id)).toEqual([2, 1]); // 99 dropped, order preserved
  });

  it("returns [] before the list has loaded", () => {
    expect(resolveRecentDatasets(undefined, [{ id: 1 }])).toEqual([]);
  });
});
