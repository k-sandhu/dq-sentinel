import { describe, expect, it } from "vitest";

import { resolveAppearance } from "./appearance";

/** A localStorage-style getter backed by a plain map. */
const getter = (map: Record<string, string>) => (k: string) => map[k] ?? null;

describe("resolveAppearance — pre-paint bootstrap contract (#171)", () => {
  it("defaults to aurora / light with nothing stored", () => {
    expect(resolveAppearance(getter({}), false)).toEqual({
      dir: "aurora",
      theme: null,
      density: null,
      font: null,
      navLayout: null,
      accent: null,
    });
  });

  // #180 regression: the pre-v2 toggle stored dq-density="comfortable" to mean the
  // 14px DEFAULT, so it must NOT become a density attribute (which would enlarge
  // the app for existing users on reload).
  it("treats legacy comfortable density as the default (no attribute)", () => {
    expect(resolveAppearance(getter({ "dq-density": "comfortable" }), false).density).toBeNull();
  });

  it("restores the valid compact / cozy / spacious densities and rejects unknown ones", () => {
    expect(resolveAppearance(getter({ "dq-density": "compact" }), false).density).toBe("compact");
    expect(resolveAppearance(getter({ "dq-density": "cozy" }), false).density).toBe("cozy");
    expect(resolveAppearance(getter({ "dq-density": "spacious" }), false).density).toBe("spacious");
    expect(resolveAppearance(getter({ "dq-density": "huge" }), false).density).toBeNull();
  });

  it("resolves theme=system against the OS preference", () => {
    expect(resolveAppearance(getter({ "dq-theme": "system" }), true).theme).toBe("dark");
    expect(resolveAppearance(getter({ "dq-theme": "system" }), false).theme).toBeNull();
  });

  // #180 review: every axis is validated against its enum — a corrupted/tampered
  // value falls back to the default rather than an unknown data-*.
  it("falls back to aurora for an unknown dir", () => {
    expect(resolveAppearance(getter({ "dq-dir": "bogus" }), false).dir).toBe("aurora");
  });

  it("validates font and nav against their enums (and the default sentinels)", () => {
    expect(resolveAppearance(getter({ "dq-font": "inter" }), false).font).toBe("inter");
    expect(resolveAppearance(getter({ "dq-font": "theme" }), false).font).toBeNull();
    expect(resolveAppearance(getter({ "dq-font": "bogus" }), false).font).toBeNull();
    expect(resolveAppearance(getter({ "dq-nav": "icons-only" }), false).navLayout).toBe("icons-only");
    expect(resolveAppearance(getter({ "dq-nav": "full" }), false).navLayout).toBeNull();
    expect(resolveAppearance(getter({ "dq-nav": "bogus" }), false).navLayout).toBeNull();
  });

  it("applies an accent only when it is a valid CSS color", () => {
    const isColor = (v: string) => v === "#0a0"; // stand-in for CSS.supports("color", v)
    expect(resolveAppearance(getter({ "dq-accent": "#0a0" }), false, isColor).accent).toBe("#0a0");
    expect(resolveAppearance(getter({ "dq-accent": "drop-table" }), false, isColor).accent).toBeNull();
  });

  it("applies explicit dark + the dir / font / nav axes together", () => {
    expect(
      resolveAppearance(
        getter({
          "dq-dir": "graphite",
          "dq-theme": "dark",
          "dq-font": "inter",
          "dq-nav": "icons-only",
        }),
        false,
      ),
    ).toEqual({
      dir: "graphite",
      theme: "dark",
      density: null,
      font: "inter",
      navLayout: "icons-only",
      accent: null,
    });
  });
});
