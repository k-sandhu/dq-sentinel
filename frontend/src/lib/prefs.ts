/**
 * Typed client-side user preferences (favorites, recently-viewed, default
 * landing, the exceptions workspace's saved views / hidden columns, and the
 * Workbench's worksheet tabs + query history).
 *
 * ── v1 storage backend: localStorage, namespaced per user ─────────────────────
 * Everything is persisted in localStorage under the PREF_KEYS below, with the
 * signed-in user's id appended (see `physicalKey`). This is a deliberate v1
 * trade-off: zero backend, instant reads.
 *
 * ── v2-swap contract (READ THIS BEFORE ADDING STORAGE) ────────────────────────
 * Enterprise users work across machines (office desktop, laptop, VDI) and will
 * eventually expect their prefs to follow them. ALL preference storage MUST go
 * through `getPref` / `setPref` (and the typed helpers that wrap them) — never
 * call `localStorage` directly from a component. That single chokepoint is the
 * contract: a future server-backed implementation (a `user_prefs` table behind
 * `GET/PUT /auth/me/prefs`) can replace the *bodies* of `getPref`/`setPref`
 * (e.g. read from an in-memory cache hydrated on login, write-through to the API)
 * without touching a single call site. Saved views are shaped `{name, params}[]`
 * so they ingest into a future `user_views` table unchanged.
 *
 * ── privacy ───────────────────────────────────────────────────────────────────
 * Store dataset IDs only — never names or row data. Prefs then carry nothing
 * meaningful without API access, so they share (and never exceed) the exposure
 * surface of the JWT that already lives in this same localStorage. The one
 * exception is the Workbench query history, which necessarily stores the SQL the
 * analyst typed — which is exactly why the per-user namespace below exists.
 */

import { getToken } from "../api/client";

export const PREF_KEYS = {
  favorites: "dq_favs", // number[] dataset ids, most-recently-starred first
  recents: "dq_recent", // {id: number, at: string}[] capped at RECENTS_CAP
  landing: "dq_landing", // LandingPref
  views: "dq_views_v1", // SavedView[] (exceptions workspace, #63)
  cols: "dq_cols_v1", // string[] of hidden column ids (exceptions table, #63)
  workbenchTabs: "dq-workbench-tabs", // WorkbenchTabsState (#104)
  workbenchHistory: "dq-workbench-history", // QueryHistoryEntry[] (#104)
} as const;

export type PrefKey = (typeof PREF_KEYS)[keyof typeof PREF_KEYS];

// ── per-user namespacing (#294) ────────────────────────────────────────────────
// On a shared VDI/desk machine the un-namespaced keys handed the next analyst the
// previous one's saved views, landing page, worksheet tabs and full SQL history.
// The namespace is derived from the JWT that is already in this same storage, NOT
// from `useAuth()`, because prefs are read synchronously during the first paint —
// long before `GET /auth/me` resolves. That removes the "user id not known yet"
// case entirely: whenever there is a session there is an id.
//
// Appearance keys (`dq-theme`, `dq-density`, …) stay deliberately un-namespaced:
// index.html's pre-paint bootstrap reads them verbatim before any token is parsed,
// and a theme choice is a machine-level display setting, not analyst state.

const NS_SEP = "::";

let nsCache: { token: string | null; ns: string | null } = { token: null, ns: null };

/** Decode the `sub` (user id) claim from the stored JWT. Signature is irrelevant
 *  here — this only picks a storage bucket; the server still validates the token
 *  on every request. Returns null when signed out or the token is unreadable. */
function currentUserNs(): string | null {
  let token: string | null = null;
  try {
    token = getToken();
  } catch {
    return null;
  }
  if (!token) return null;
  if (nsCache.token === token) return nsCache.ns;

  let ns: string | null = null;
  try {
    const payload = token.split(".")[1];
    if (payload) {
      const b64 = payload.replace(/-/g, "+").replace(/_/g, "/");
      const json = atob(b64.padEnd(Math.ceil(b64.length / 4) * 4, "="));
      const sub = (JSON.parse(json) as { sub?: unknown }).sub;
      if (typeof sub === "string" && sub) ns = sub;
      else if (typeof sub === "number") ns = String(sub);
    }
  } catch {
    ns = null; // hand-edited / foreign token — fall back to the shared bucket
  }
  nsCache = { token, ns };
  return ns;
}

/** Storage key for a logical pref key: per-user while signed in, the bare
 *  (pre-namespacing) key when signed out. */
function physicalKey(key: string): string {
  const ns = currentUserNs();
  return ns ? `${key}${NS_SEP}${ns}` : key;
}

/**
 * One-time adoption of a value written before namespacing existed. The first
 * signed-in reader claims the legacy key and *removes* it, so a second analyst on
 * the same machine can't inherit it too. Single-user machines therefore keep their
 * prefs across the upgrade; on a genuinely shared machine whoever logs in first
 * adopts them once — which is no worse than the status quo it replaces.
 */
function adoptLegacy(key: string, physical: string): string | null {
  const legacy = localStorage.getItem(key);
  if (legacy == null) return null;
  localStorage.setItem(physical, legacy);
  localStorage.removeItem(key);
  return legacy;
}

/** Read a JSON-serialized preference. Returns `fallback` on miss or any error. */
export function getPref<T>(key: string, fallback: T): T {
  try {
    const physical = physicalKey(key);
    let raw = localStorage.getItem(physical);
    if (raw == null && physical !== key) raw = adoptLegacy(key, physical);
    return raw == null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export interface SetPrefOptions {
  /** Fire the `dq:prefs` window event (default true). Pass `false` for
   *  high-frequency writes nothing subscribes to — the Workbench re-persists its
   *  worksheet SQL on every keystroke, and waking every `subscribePrefs` listener
   *  per character would re-render the sidebar while the analyst types. */
  notify?: boolean;
}

/**
 * Write a JSON-serialized preference. Degrades silently when storage is
 * unavailable (private mode / quota). Dispatches a `dq:prefs` window event so
 * other mounted components (e.g. the sidebar Favorites group) can re-read
 * without a remount — see `subscribePrefs`.
 */
export function setPref<T>(key: string, value: T, opts: SetPrefOptions = {}): void {
  try {
    localStorage.setItem(physicalKey(key), JSON.stringify(value));
  } catch {
    /* storage unavailable — degrade silently */
  }
  if (opts.notify === false) return;
  try {
    window.dispatchEvent(new CustomEvent(PREFS_EVENT, { detail: { key } }));
  } catch {
    /* no window (SSR/tests) — nothing to notify */
  }
}

/** Window event fired by `setPref`; lets live components stay in sync. */
export const PREFS_EVENT = "dq:prefs";

/** Subscribe to in-tab preference changes. Returns an unsubscribe fn. */
export function subscribePrefs(handler: () => void): () => void {
  window.addEventListener(PREFS_EVENT, handler);
  return () => window.removeEventListener(PREFS_EVENT, handler);
}

// ── raw (non-JSON) prefs ────────────────────────────────────────────────────────
// The appearance axes (#172) store plain strings under `dq-*` keys that the
// pre-paint bootstrap in index.html reads VERBATIM, so they can't go through the
// JSON-encoding getPref/setPref — and for the same reason they are NOT per-user
// namespaced (#294): the bootstrap runs before any token is parsed, and making it
// user-aware would trade a flash-of-wrong-theme for a leak that is cosmetic only.
// These raw helpers keep them on the same prefs chokepoint (one place to swap for
// a server-backed store) and fire the same `dq:prefs` event so live components
// (the appearance drawer) stay in sync.

/** Read a raw string preference, or `null` on miss / unavailable storage. */
export function getRawPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Write (or, with `null`, remove) a raw string preference; fires `dq:prefs`. */
export function setRawPref(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable — degrade silently */
  }
  try {
    window.dispatchEvent(new CustomEvent(PREFS_EVENT, { detail: { key } }));
  } catch {
    /* no window (SSR/tests) — nothing to notify */
  }
}

// ── saved views (exceptions workspace, #63) ────────────────────────────────────

/** A persisted saved view: a name + a URL search-param string. */
export interface SavedView {
  name: string;
  params: string;
}

// ── landing page ──────────────────────────────────────────────────────────────

/**
 * The fixed, named landing destinations offered by the Settings dropdown
 * (#59). Kept as a closed union so `LANDING_OPTIONS` stays exhaustive.
 */
export type NamedLanding = "/" | "/exceptions" | "/datasets" | "/workbench";

/**
 * The stored landing preference. Besides the named pages above, custom
 * dashboards (#68) let a user pin a specific board (e.g. "/dashboards/3") as
 * their landing page, so the stored value is any in-app path. The `(string & {})`
 * arm widens to arbitrary paths while preserving autocomplete for the named ones.
 */
export type LandingPref = NamedLanding | (string & {});

export const LANDING_OPTIONS: { value: NamedLanding; label: string }[] = [
  { value: "/", label: "Home" },
  { value: "/exceptions", label: "Exceptions" },
  { value: "/datasets", label: "Datasets" },
  { value: "/workbench", label: "Workbench" },
];

export function getLanding(): LandingPref {
  return getPref<LandingPref>(PREF_KEYS.landing, "/");
}

export function setLanding(value: LandingPref): void {
  setPref(PREF_KEYS.landing, value);
}

/**
 * Reset the landing preference back to Home. Used when the pinned destination
 * disappears — e.g. deleting the custom dashboard (#68) that was set as landing,
 * so a fresh tab doesn't loop into a 404.
 */
export function clearLanding(): void {
  setPref(PREF_KEYS.landing, "/");
}

// ── favorites ─────────────────────────────────────────────────────────────────

/** Max favorites surfaced in the sidebar group. */
export const FAVORITES_SIDEBAR_CAP = 6;

export function getFavorites(): number[] {
  const raw = getPref<unknown[]>(PREF_KEYS.favorites, []);
  // Defensive: only keep finite numbers (storage may have been hand-edited).
  return raw.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
}

export function isFavorite(id: number): boolean {
  return getFavorites().includes(id);
}

/**
 * Toggle a dataset's favorite state. Newly-starred ids go to the FRONT so the
 * ordering is most-recently-starred first (no manual reordering in v1).
 * Returns the new favorited state.
 */
export function toggleFavorite(id: number): boolean {
  const current = getFavorites();
  const has = current.includes(id);
  const next = has ? current.filter((x) => x !== id) : [id, ...current];
  setPref(PREF_KEYS.favorites, next);
  return !has;
}

// ── recently viewed ───────────────────────────────────────────────────────────

export interface RecentEntry {
  id: number;
  at: string; // ISO timestamp of the visit
}

/** Max entries kept in the recents list. */
export const RECENTS_CAP = 8;

export function getRecents(): RecentEntry[] {
  const raw = getPref<RecentEntry[]>(PREF_KEYS.recents, []);
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (e): e is RecentEntry =>
      !!e && typeof e.id === "number" && Number.isFinite(e.id) && typeof e.at === "string",
  );
}

/**
 * Record a dataset visit: move it to the front (most-recent-first), dedupe by
 * id, and cap the list at RECENTS_CAP.
 */
export function pushRecent(id: number): void {
  const rest = getRecents().filter((e) => e.id !== id);
  const next: RecentEntry[] = [{ id, at: new Date().toISOString() }, ...rest].slice(0, RECENTS_CAP);
  setPref(PREF_KEYS.recents, next);
}

// ── stale-id pruning ──────────────────────────────────────────────────────────

/**
 * Drop favorites/recents whose dataset no longer exists. Datasets get deleted in
 * real deployments; dead entries that reappear every session read as bugs, so we
 * prune storage (not just the rendered view) the moment we have the live id set.
 * No-ops (and writes nothing) when everything is still valid, to avoid spurious
 * `dq:prefs` events. Call once the live datasets list has loaded.
 */
export function pruneStalePrefs(liveIds: Iterable<number>): void {
  const live = new Set(liveIds);

  const favs = getFavorites();
  const favsKept = favs.filter((id) => live.has(id));
  if (favsKept.length !== favs.length) setPref(PREF_KEYS.favorites, favsKept);

  const recents = getRecents();
  const recentsKept = recents.filter((e) => live.has(e.id));
  if (recentsKept.length !== recents.length) setPref(PREF_KEYS.recents, recentsKept);
}
