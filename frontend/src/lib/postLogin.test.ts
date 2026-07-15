import { describe, expect, it } from "vitest";

import { resolvePostLoginTarget, safeInternalPath } from "./postLogin";

describe("safeInternalPath", () => {
  it("passes through same-app absolute paths with query and hash", () => {
    expect(safeInternalPath("/checks")).toBe("/checks");
    expect(safeInternalPath("/exceptions?dataset_id=5&status=open")).toBe(
      "/exceptions?dataset_id=5&status=open",
    );
    expect(safeInternalPath("/datasets/3/checks#top")).toBe("/datasets/3/checks#top");
  });

  it("falls back to '/' for non-strings and missing state", () => {
    expect(safeInternalPath(undefined)).toBe("/");
    expect(safeInternalPath(null)).toBe("/");
    expect(safeInternalPath(42)).toBe("/");
    expect(safeInternalPath({ pathname: "/checks" })).toBe("/");
  });

  it("rejects external / scheme-relative / relative targets (no open redirect)", () => {
    expect(safeInternalPath("https://evil.example")).toBe("/");
    expect(safeInternalPath("//evil.example/phish")).toBe("/");
    expect(safeInternalPath("checks")).toBe("/");
    expect(safeInternalPath("")).toBe("/");
  });

  it("never loops back to /login", () => {
    expect(safeInternalPath("/login")).toBe("/");
    expect(safeInternalPath("/login?next=x")).toBe("/");
  });
});

describe("resolvePostLoginTarget", () => {
  it("prefers router state over the ?from= query", () => {
    expect(resolvePostLoginTarget("/checks", "?from=%2Fruns")).toBe("/checks");
  });

  it("falls back to ?from= (the API client's hard 401 redirect)", () => {
    expect(resolvePostLoginTarget(undefined, "?from=%2Fexceptions%3Fdataset_id%3D5")).toBe(
      "/exceptions?dataset_id=5",
    );
    expect(resolvePostLoginTarget(null, "?from=%2Fdatasets%2F3%2Fchecks")).toBe(
      "/datasets/3/checks",
    );
  });

  it("validates both channels — junk yields '/'", () => {
    expect(resolvePostLoginTarget(undefined, "?from=https%3A%2F%2Fevil.example")).toBe("/");
    expect(resolvePostLoginTarget(undefined, "?from=%2F%2Fevil.example")).toBe("/");
    expect(resolvePostLoginTarget(undefined, "?from=%2Flogin")).toBe("/");
    expect(resolvePostLoginTarget(undefined, "")).toBe("/");
  });
});
