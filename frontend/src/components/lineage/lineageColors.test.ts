import { describe, expect, it } from "vitest";

import { edgeTone, healthColor } from "./lineageColors";

describe("healthColor", () => {
  it("maps each health to its status token", () => {
    expect(healthColor("fail")).toBe("var(--danger)");
    expect(healthColor("warn")).toBe("var(--warn-strong)");
    expect(healthColor("pass")).toBe("var(--ok)");
    expect(healthColor("unknown")).toBe("var(--slate)");
    expect(healthColor(null)).toBe("var(--slate)");
    expect(healthColor(undefined)).toBe("var(--slate)");
  });

  // The whole point of D4: returns CSS variable references only, so the MiniMap +
  // nodes re-skin with the theme and no raw hex leaks back in.
  it("returns only CSS variable references, never a raw hex", () => {
    for (const h of ["fail", "warn", "pass", "x", null, undefined]) {
      expect(healthColor(h)).toMatch(/^var\(--[a-z-]+\)$/);
      expect(healthColor(h)).not.toMatch(/#[0-9a-fA-F]/);
    }
  });
});

describe("edgeTone", () => {
  it("maps each edge kind to its token", () => {
    expect(edgeTone("aggregate")).toBe("var(--purple)");
    expect(edgeTone("derived")).toBe("var(--brand-dark)");
    expect(edgeTone("unresolved")).toBe("var(--warn-strong)");
    expect(edgeTone("direct")).toBe("var(--slate)");
    expect(edgeTone(undefined)).toBe("var(--slate)");
  });

  it("never returns a raw hex", () => {
    for (const k of ["aggregate", "derived", "unresolved", "direct", undefined]) {
      expect(edgeTone(k)).not.toMatch(/#[0-9a-fA-F]/);
    }
  });
});
