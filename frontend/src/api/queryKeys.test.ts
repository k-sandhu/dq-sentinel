import { describe, expect, it } from "vitest";

import { qk } from "./queryKeys";

describe("query-key factory (qk)", () => {
  // The invariant that makes partial-key invalidation work: every key an accessor
  // produces must sit under its family's `all` prefix, so `invalidateQueries({
  // queryKey: qk.<family>.all })` still matches every variant after migration.
  it("every accessor's key starts with its family's `all` prefix", () => {
    for (const [family, accessors] of Object.entries(qk)) {
      const group = accessors as Record<string, unknown>;
      const all = group.all as readonly unknown[];
      expect(Array.isArray(all), `${family}.all is a tuple`).toBe(true);
      expect(all.length, `${family}.all is a single-segment prefix`).toBe(1);

      for (const [name, value] of Object.entries(group)) {
        if (name === "all" || typeof value !== "function") continue;
        // Extra args are ignored by accessors with fewer params, so one spread
        // exercises every arity; we only assert the prefix, not arg semantics.
        const key = (value as (...a: unknown[]) => readonly unknown[])(1, 1, 1, 1);
        expect(Array.isArray(key), `${family}.${name}() returns a tuple`).toBe(true);
        expect(key[0], `${family}.${name}() sits under ${family}.all`).toBe(all[0]);
      }
    }
  });

  // Accessors must return a FRESH array each call (no shared mutable tuple) so a
  // caller can never accidentally mutate a key another query is using.
  it("accessors return a fresh array each call (equal value, distinct identity)", () => {
    const a = qk.datasets.detail(7);
    const b = qk.datasets.detail(7);
    expect(a).toEqual(b);
    expect(a).not.toBe(b);
  });

  // Exact-shape spot checks for the keys most likely to drift during migration:
  // object segments must hash identically, and segment order must be preserved.
  it("reproduces the exact observed key shapes", () => {
    expect(qk.datasets.list()).toEqual(["datasets"]);
    expect(qk.datasets.detail(7)).toEqual(["datasets", 7]);
    expect(qk.datasets.byConnection(3)).toEqual(["datasets", { connectionId: 3 }]);

    // checks: two divergent shapes preserved on purpose (object vs bare-id).
    expect(qk.checks.byDatasetObj(5)).toEqual(["checks", { datasetId: 5 }]);
    expect(qk.checks.byDatasetId(5)).toEqual(["checks", 5]);
    expect(qk.checks.active()).toEqual(["checks", "active"]);

    // runs: list-by-querystring vs detail-by-id share a 2-segment shape but differ
    // by value type; object-scoped variants stay objects.
    expect(qk.runs.list("?dataset_id=2")).toEqual(["runs", "?dataset_id=2"]);
    expect(qk.runs.byCheck(9)).toEqual(["runs", { checkId: 9 }]);

    // multi-segment + sub-key keys.
    expect(qk.contractDiff.detail(1, 2, 4, 3)).toEqual([
      "contract-diff",
      1,
      2,
      4,
      3,
    ]);
    expect(qk.audit.list("dataset", "update", 24, 50)).toEqual([
      "audit",
      "dataset",
      "update",
      24,
      50,
    ]);
    expect(qk.dashboard.console()).toEqual(["dashboard", "console"]);
    expect(qk.scorecards.summary()).toEqual(["scorecards", "summary"]);
    expect(qk.globalSearch.detail("orders")).toEqual(["global-search", "orders"]);
    expect(
      qk.suggest.detail({
        connectionId: 1,
        datasetId: null,
        runId: null,
        exceptionId: null,
        checkId: null,
      }),
    ).toEqual([
      "suggest",
      { connectionId: 1, datasetId: null, runId: null, exceptionId: null, checkId: null },
    ]);
  });

  // The two no-arg shapes that must coincide so query (`.list/.get`) and
  // invalidation (`.all`) hit the same cache entry.
  it("`all` and the no-arg accessor coincide where the key has no extra segment", () => {
    expect(qk.datasets.list()).toEqual([...qk.datasets.all]);
    expect(qk.health.get()).toEqual([...qk.health.all]);
    expect(qk.fleetHealth.list()).toEqual([...qk.fleetHealth.all]);
    expect(qk.dashboard.summary()).toEqual([...qk.dashboard.all]);
  });
});
