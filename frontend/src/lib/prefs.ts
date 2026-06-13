/**
 * Typed client-side user preferences (favorites, recently-viewed, default landing).
 *
 * ── v1 storage backend: localStorage ──────────────────────────────────────────
 * Everything is persisted in localStorage, namespaced under the PREF_KEYS below.
 * This is a deliberate v1 trade-off: zero backend, instant reads, no migration.
 *
 * ── v2-swap contract (READ THIS BEFORE ADDING STORAGE) ────────────────────────
 * Enterprise users work across machines (office desktop, laptop, VDI) and will
 * eventually expect their prefs to follow them. ALL preference storage MUST go
 * through `getPref` / `setPref` (and the typed helpers that wrap them) — never
 * call `localStorage` directly from a component. That single chokepoint is the
 * contract: a future server-backed implementation (a `user_prefs` table behind
 * `GET/PUT /auth/me/prefs`) can replace the *bodies* of `getPref`/`setPref`
 * (e.g. read from an in-memory cache hydrated on login, write-through to the API)
 * without touching a single call site.
 *
 * ── privacy ───────────────────────────────────────────────────────────────────
 * Store dataset IDs only — never names or row data. Prefs then carry nothing
 * meaningful without API access, so they share (and never exceed) the exposure
 * surface of the JWT that already lives in this same localStorage.
 */

/** Read a JSON-serialized preference. Returns `fallback` on miss or any error. */
export function getPref<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

/**
 * Write a JSON-serialized preference. Degrades silently when storage is
 * unavailable (private mode / quota). Dispatches a `dq:prefs` window event so
 * other mounted components (e.g. the sidebar Favorites group) can re-read
 * without a remount — see `subscribePrefs`.
 */
export function setPref<T>(key: string, value: T): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable — degrade silently */
  }
  try {
    window.dispatchEvent(new CustomEvent(PREFS_EVENT, { detail: { key } }));
  } catch {
    /* no window (SSR/tests) — nothing to notify */
  }
}

export const PREF_KEYS = {
  favorites: "dq_favs", // number[] dataset ids, most-recently-starred first
  recents: "dq_recent", // {id: number, at: string}[] capped at RECENTS_CAP
  landing: "dq_landing", // LandingPref
} as const;

/** Window event fired by `setPref`; lets live components stay in sync. */
export const PREFS_EVENT = "dq:prefs";

/** Subscribe to in-tab preference changes. Returns an unsubscribe fn. */
export function subscribePrefs(handler: () => void): () => void {
  window.addEventListener(PREFS_EVENT, handler);
  return () => window.removeEventListener(PREFS_EVENT, handler);
}

// ── landing page ──────────────────────────────────────────────────────────────

export type LandingPref = "/" | "/exceptions" | "/datasets" | "/workbench";

export const LANDING_OPTIONS: { value: LandingPref; label: string }[] = [
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
