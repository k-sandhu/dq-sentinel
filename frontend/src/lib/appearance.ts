/**
 * Appearance system (#171 bootstrap contract + #172 drawer).
 *
 * `resolveAppearance` is MIRRORED by the inline `<script>` in `index.html`: it must
 * run before the bundle loads (to avoid a flash / layout shift) so it can't import a
 * module. Keep the two in sync — this module is the tested contract
 * (appearance.test.ts). The drawer (#172) is the writer of these `dq-*` keys via the
 * `getAxis`/`setAxis` helpers below, which route through the prefs.ts chokepoint.
 *
 * Every axis is validated against its known enum so a corrupted / tampered
 * localStorage value falls back to the default instead of producing an unknown
 * `data-*` the CSS won't recognize (#180 review). A `null` value means "leave the
 * attribute unset" (the theme/axis default).
 */
import { getRawPref, setRawPref } from "./prefs";

export interface ResolvedAppearance {
  dir: string;
  theme: "dark" | null;
  density: string | null;
  font: string | null;
  navLayout: string | null;
  accent: string | null;
}

// "comfortable" is intentionally NOT a valid density: the pre-v2 toggle persisted it
// to mean the 14px DEFAULT, so it must resolve to "no attribute" (#180 review).
// "spacious" is the looser level the drawer adds (#172) — a distinct name precisely
// so the legacy "comfortable" value can never collide with it.
const DIRS = new Set(["aurora", "graphite", "editorial"]);
const DENSITIES = new Set(["compact", "cozy", "spacious"]);
const FONTS = new Set(["inter", "system", "rounded"]);
const NAV_LAYOUTS = new Set(["icons", "centered"]); // "full" = default (unset)

const inSet = (set: Set<string>, value: string | null): string | null =>
  value && set.has(value) ? value : null;

export function resolveAppearance(
  get: (key: string) => string | null,
  prefersDarkNow: boolean,
  isColor: (value: string) => boolean = () => true,
): ResolvedAppearance {
  let mode = get("dq-theme") || "system"; // light | dark | system — system is the default
  if (mode === "system") mode = prefersDarkNow ? "dark" : "light";

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

// ── appearance drawer axes (#172) ────────────────────────────────────────────────
// One source of truth for the drawer: storage key, the `<html>` attribute it drives,
// the default ("unset") value, and the labelled options. `mode` is special — it maps
// to `data-theme` via light/dark/system resolution rather than a literal attribute.

export type AxisName = "dir" | "mode" | "density" | "font" | "nav";

interface AxisSpec {
  key: string; // localStorage key (read verbatim by the bootstrap)
  attr: string; // documentElement.dataset[attr] -> data-<kebab>
  def: string; // default selection (the theme/axis default, stored as null)
  label: string;
  options: { value: string; label: string }[];
}

export const AXES: Record<AxisName, AxisSpec> = {
  dir: {
    key: "dq-dir",
    attr: "dir",
    def: "aurora",
    label: "Theme",
    options: [
      { value: "aurora", label: "Aurora" },
      { value: "graphite", label: "Graphite" },
      { value: "editorial", label: "Editorial" },
    ],
  },
  mode: {
    key: "dq-theme",
    attr: "theme",
    def: "system", // default follows the OS (#172 / #181 review)
    label: "Mode",
    options: [
      { value: "light", label: "Light" },
      { value: "dark", label: "Dark" },
      { value: "system", label: "System" },
    ],
  },
  density: {
    key: "dq-density",
    attr: "density",
    def: "cozy",
    label: "Density",
    options: [
      { value: "compact", label: "Compact" },
      { value: "cozy", label: "Cozy" },
      { value: "spacious", label: "Spacious" },
    ],
  },
  font: {
    key: "dq-font",
    attr: "font",
    def: "theme",
    label: "Font",
    options: [
      { value: "theme", label: "Theme" },
      { value: "inter", label: "Inter" },
      { value: "system", label: "System" },
      { value: "rounded", label: "Rounded" },
    ],
  },
  nav: {
    key: "dq-nav",
    attr: "navLayout",
    def: "full",
    label: "Navigation",
    options: [
      { value: "full", label: "Full" },
      { value: "icons", label: "Icons" },
      { value: "centered", label: "Centered" },
    ],
  },
};

export function prefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    !!window.matchMedia?.("(prefers-color-scheme: dark)").matches
  );
}

/** Current stored value for an axis, or its default when unset / invalid. */
export function getAxis(name: AxisName): string {
  const spec = AXES[name];
  const v = getRawPref(spec.key);
  return v && spec.options.some((o) => o.value === v) ? v : spec.def;
}

/** Apply a mode selection (light | dark | system) to `<html data-theme>`. */
export function applyMode(mode: string): void {
  const d = document.documentElement;
  const dark = mode === "dark" || (mode === "system" && prefersDark());
  if (dark) d.dataset.theme = "dark";
  else delete d.dataset.theme;
}

function applyAxis(name: AxisName, value: string): void {
  if (name === "mode") {
    applyMode(value);
    return;
  }
  const d = document.documentElement;
  const { attr, def } = AXES[name];
  // `dir` is always set explicitly (aurora is a real value, mirroring the bootstrap);
  // the other axes delete their attribute for the default/"unset" value.
  if (name !== "dir" && value === def) delete d.dataset[attr];
  else d.dataset[attr] = value;
}

/** Set an axis: persist it (raw string + `dq:prefs`) and apply it live to `<html>`. */
export function setAxis(name: AxisName, value: string): void {
  const spec = AXES[name];
  // Store the default as `null` (clean storage, matches the bootstrap's "unset"),
  // except `mode` where an explicit "light" choice must persist as a real value.
  const stored = name !== "mode" && value === spec.def ? null : value;
  setRawPref(spec.key, stored);
  applyAxis(name, value);
}

// ── accent ───────────────────────────────────────────────────────────────────────
const DEFAULT_ACCENT = "#509ee3";
const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

/** The user's accent override, or `null` when none is set (theme brand applies). */
export function getAccent(): string | null {
  return getRawPref("dq-accent");
}

/** Colour to show in the accent picker: the override, else the live `--brand`. */
export function accentSwatch(): string {
  const override = getAccent();
  if (override && HEX.test(override)) return override;
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue("--brand").trim();
    return HEX.test(v) ? v : DEFAULT_ACCENT;
  } catch {
    return DEFAULT_ACCENT;
  }
}

/** Apply (or, with `null`, clear) the accent on `<html>`. A custom accent also
 *  RE-DERIVES the related brand tokens so hover/active (`--brand-dark`) and tinted
 *  surfaces (`--brand-light`) follow it instead of staying on the old theme brand
 *  (#181 review). The derivations mix toward `--text-dark` / `--card`, which both
 *  flip per light/dark mode, so the single formula stays correct in both modes.
 *  (`--brand-ghost` / `--ring` already derive from `--brand` in the CSS.) */
function applyAccent(value: string | null): void {
  const d = document.documentElement;
  const ok = !!value && typeof CSS !== "undefined" && CSS.supports("color", value);
  if (ok) {
    d.style.setProperty("--brand", value as string);
    d.style.setProperty("--brand-dark", "color-mix(in srgb, var(--brand) 75%, var(--text-dark))");
    d.style.setProperty("--brand-light", "color-mix(in srgb, var(--brand) 16%, var(--card))");
  } else {
    d.style.removeProperty("--brand");
    d.style.removeProperty("--brand-dark");
    d.style.removeProperty("--brand-light");
  }
}

/** Set (or, with `null`, clear) the accent override — validated, applied, persisted. */
export function setAccent(value: string | null): void {
  const ok = !!value && typeof CSS !== "undefined" && CSS.supports("color", value);
  applyAccent(ok ? value : null);
  setRawPref("dq-accent", ok ? value : null);
}

/** Re-apply every persisted axis (+ accent) to `<html>` from current storage.
 *  Mirrors the pre-paint bootstrap; used to live-sync another tab's changes via the
 *  `storage` event so an already-open tab updates without a reload (#181 review). */
export function applyAppearance(): void {
  (Object.keys(AXES) as AxisName[]).forEach((name) => applyAxis(name, getAxis(name)));
  applyAccent(getAccent());
}
