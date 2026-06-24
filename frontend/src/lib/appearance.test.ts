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

  it("still restores a stored compact density", () => {
    expect(resolveAppearance(getter({ "dq-density": "compact" }), false).density).toBe("compact");
  });

  it("restores the new cozy density", () => {
    expect(resolveAppearance(getter({ "dq-density": "cozy" }), false).density).toBe("cozy");
  });

  it("resolves theme=system against the OS preference", () => {
    expect(resolveAppearance(getter({ "dq-theme": "system" }), true).theme).toBe("dark");
    expect(resolveAppearance(getter({ "dq-theme": "system" }), false).theme).toBeNull();
  });

  it("applies explicit dark + the dir / font / nav / accent axes", () => {
    expect(
      resolveAppearance(
        getter({
          "dq-dir": "graphite",
          "dq-theme": "dark",
          "dq-font": "inter",
          "dq-nav": "icons-only",
          "dq-accent": "#ff0000",
        }),
        false,
      ),
    ).toEqual({
      dir: "graphite",
      theme: "dark",
      density: null,
      font: "inter",
      navLayout: "icons-only",
      accent: "#ff0000",
    });
  });

  it("ignores the default sentinels (font=theme, nav=full)", () => {
    const r = resolveAppearance(getter({ "dq-font": "theme", "dq-nav": "full" }), false);
    expect(r.font).toBeNull();
    expect(r.navLayout).toBeNull();
  });
});
