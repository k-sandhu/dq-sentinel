/**
 * Canonical resolver for the pre-paint appearance bootstrap (#171).
 *
 * The inline `<script>` in `index.html` MIRRORS this function: it must run before
 * the bundle loads (to avoid a flash / layout shift) so it can't import a module.
 * Keep the two in sync — this module is the tested contract (appearance.test.ts),
 * and #172 (the appearance drawer / prefs.ts) is the writer of these `dq-*` keys.
 *
 * A `null` value means "leave the attribute unset" (the theme/axis default).
 */
export interface ResolvedAppearance {
  dir: string;
  theme: "dark" | null;
  density: string | null;
  font: string | null;
  navLayout: string | null;
  accent: string | null;
}

export function resolveAppearance(
  get: (key: string) => string | null,
  prefersDark: boolean,
): ResolvedAppearance {
  let mode = get("dq-theme") || "light"; // light | dark | system
  if (mode === "system") mode = prefersDark ? "dark" : "light";

  const density = get("dq-density");
  const font = get("dq-font");
  const nav = get("dq-nav");
  const accent = get("dq-accent");

  return {
    dir: get("dq-dir") || "aurora",
    theme: mode === "dark" ? "dark" : null,
    // The pre-v2 density toggle persisted "comfortable" to mean the DEFAULT (14px,
    // no attribute). Never promote it to a density attribute (#180 review).
    density: density && density !== "comfortable" ? density : null,
    font: font && font !== "theme" ? font : null,
    navLayout: nav && nav !== "full" ? nav : null,
    accent: accent || null,
  };
}
