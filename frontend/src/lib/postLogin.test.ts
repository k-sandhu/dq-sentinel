import { describe, expect, it } from "vitest";

import { safeInternalPath } from "./postLogin";

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
