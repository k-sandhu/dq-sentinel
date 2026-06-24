/**
 * Canonical resolver for the pre-paint appearance bootstrap (#171).
 *
 * The inline `<script>` in `index.html` MIRRORS this function: it must run before
 * the bundle loads (to avoid a flash / layout shift) so it can't import a module.
 * Keep the two in sync — this module is the tested contract (appearance.test.ts),
 * and #172 (the appearance drawer / prefs.ts) is the writer of these `dq-*` keys.
 *
 * Every axis is validated against its known enum so a corrupted / tampered
 * localStorage value falls back to the default instead of producing an unknown
 * `data-*` the CSS won't recognize (#180 review). A `null` value means "leave the
 * attribute unset" (the theme/axis default).
 */
export interface ResolvedAppearance {
  dir: string;
  theme: "dark" | null;
  density: string | null;
  font: string | null;
  navLayout: string | null;
  accent: string | null;
}

// "comfortable" is intentionally NOT a valid density: the pre-v2 toggle persisted
// it to mean the 14px DEFAULT, so it must resolve to "no attribute" (#180 review).
const DIRS = new Set(["aurora", "graphite", "editorial"]);
const DENSITIES = new Set(["compact", "cozy"]);
const FONTS = new Set(["inter", "system", "rounded"]);
const NAV_LAYOUTS = new Set(["icons-only", "centered"]); // "full" = default (unset)

const inSet = (set: Set<string>, value: string | null): string | null =>
  value && set.has(value) ? value : null;

export function resolveAppearance(
  get: (key: string) => string | null,
  prefersDark: boolean,
  isColor: (value: string) => boolean = () => true,
): ResolvedAppearance {
  let mode = get("dq-theme") || "light"; // light | dark | system
  if (mode === "system") mode = prefersDark ? "dark" : "light";

  const accent = get("dq-accent");

  return {
    dir: inSet(DIRS, get("dq-dir")) || "aurora",
    theme: mode === "dark" ? "dark" : null,
    density: inSet(DENSITIES, get("dq-density")),
    font: inSet(FONTS, get("dq-font")),
    navLayout: inSet(NAV_LAYOUTS, get("dq-nav")),
    accent: accent && isColor(accent) ? accent : null,
  };
}
