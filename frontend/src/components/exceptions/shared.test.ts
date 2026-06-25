import { describe, expect, it } from "vitest";

import { parseFilters, seenSinceToIso, toApiParams } from "./shared";

describe("parseFilters", () => {
  it("reads multi-value + scalar params", () => {
    const sp = new URLSearchParams(
      "status=open&status=acknowledged&severity=error&q=null&sort=oldest&offset=50&group=dataset&sel=12&assignee=me",
    );
    const f = parseFilters(sp);
    expect(f.status).toEqual(["open", "acknowledged"]);
    expect(f.severity).toEqual(["error"]);
    expect(f.q).toBe("null");
    expect(f.sort).toBe("oldest");
    expect(f.offset).toBe(50);
    expect(f.group).toBe("dataset");
    expect(f.sel).toBe(12);
    expect(f.assignee).toBe("me");
  });

  it("applies defaults: sort=newest, offset=0, group=none, sel=null, empty arrays", () => {
    const f = parseFilters(new URLSearchParams(""));
    expect(f.sort).toBe("newest");
    expect(f.offset).toBe(0);
    expect(f.group).toBe("none");
    expect(f.sel).toBeNull();
    expect(f.status).toEqual([]);
    expect(f.severity).toEqual([]);
  });
});

describe("toApiParams", () => {
  it("pins dataset/run/check ids and appends the multi-value filters", () => {
    const f = parseFilters(
      new URLSearchParams(
        "status=open&status=muted&severity=warn&check_type=not_null&assignee=me&recurrence=new&q=foo",
      ),
    );
    const p = toApiParams(f, { datasetId: 3, runId: 7, checkId: 9 });
    expect(p.get("dataset_id")).toBe("3");
    expect(p.get("run_id")).toBe("7");
    expect(p.get("check_id")).toBe("9");
    expect(p.getAll("status")).toEqual(["open", "muted"]);
    expect(p.getAll("severity")).toEqual(["warn"]);
    expect(p.get("check_type")).toBe("not_null");
    expect(p.get("assignee")).toBe("me");
    expect(p.get("recurrence")).toBe("new");
    expect(p.get("q")).toBe("foo");
  });

  it("omits sort when it is the default 'newest' (keeps the URL clean)", () => {
    expect(toApiParams(parseFilters(new URLSearchParams("sort=newest"))).has("sort")).toBe(false);
    expect(toApiParams(parseFilters(new URLSearchParams("sort=severity"))).get("sort")).toBe("severity");
  });

  // The triage workspace survives a reload: the URL filters round-trip through the
  // API params unchanged (#63 BF-2 — the bug this guards against).
  it("round-trips URL filters into API params without dropping the pinned selection", () => {
    const sp = new URLSearchParams("status=open&severity=error&q=orders&assignee=none");
    const p = toApiParams(parseFilters(sp));
    expect(p.getAll("status")).toEqual(["open"]);
    expect(p.getAll("severity")).toEqual(["error"]);
    expect(p.get("q")).toBe("orders");
    expect(p.get("assignee")).toBe("none");
  });
});

describe("seenSinceToIso", () => {
  it("returns null for the 'any time' preset", () => {
    expect(seenSinceToIso("")).toBeNull();
  });

  it("maps each preset to an ISO timestamp the right distance in the past", () => {
    const now = Date.now();
    const hoursAgo = (preset: "24h" | "7d" | "30d") =>
      (now - new Date(seenSinceToIso(preset) as string).getTime()) / (3600 * 1000);
    expect(Math.round(hoursAgo("24h"))).toBe(24);
    expect(Math.round(hoursAgo("7d"))).toBe(24 * 7);
    expect(Math.round(hoursAgo("30d"))).toBe(24 * 30);
  });
});
