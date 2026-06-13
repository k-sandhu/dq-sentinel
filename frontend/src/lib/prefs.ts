/**
 * Personalization storage boundary.
 *
 * Keep every personalization read/write in this module. v1 stores only opaque
 * dataset ids and landing route strings in localStorage; a future user_prefs
 * API can replace getPref/setPref without touching call sites.
 */

export const PREF_EVENT = "dq:prefs";

export const PREF_KEYS = {
  favorites: "dq_favs",
  recents: "dq_recent",
  landing: "dq_landing",
} as const;

export const SESSION_KEYS = {
  landed: "dq_landed",
} as const;

export const LANDING_PATHS = ["/", "/exceptions", "/datasets", "/workbench"] as const;
export type LandingPath = (typeof LANDING_PATHS)[number];

export interface RecentDataset {
  id: number;
  at: string;
}

const RECENT_LIMIT = 8;
const PREF_KEY_VALUES = Object.values(PREF_KEYS) as string[];
let memoryLanded = false;

export function getPref<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw == null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function setPref<T>(key: string, value: T): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: degrade silently */
  }
  emitPrefsChanged(key);
}

export function subscribePrefs(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;

  const onPrefs = () => listener();
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || PREF_KEY_VALUES.includes(event.key)) listener();
  };

  window.addEventListener(PREF_EVENT, onPrefs);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(PREF_EVENT, onPrefs);
    window.removeEventListener("storage", onStorage);
  };
}

export function getFavoriteDatasetIds(): number[] {
  return normalizeIds(getPref<unknown>(PREF_KEYS.favorites, []));
}

export function setFavoriteDatasetIds(ids: number[]): number[] {
  const next = normalizeIds(ids);
  setPref(PREF_KEYS.favorites, next);
  return next;
}

export function toggleFavoriteDataset(id: number): number[] {
  const favoriteIds = getFavoriteDatasetIds();
  const next = favoriteIds.includes(id) ? favoriteIds.filter((favoriteId) => favoriteId !== id) : [id, ...favoriteIds];
  return setFavoriteDatasetIds(next);
}

export function getRecentDatasets(): RecentDataset[] {
  return normalizeRecents(getPref<unknown>(PREF_KEYS.recents, []));
}

export function setRecentDatasets(recents: RecentDataset[]): RecentDataset[] {
  const next = normalizeRecents(recents);
  setPref(PREF_KEYS.recents, next);
  return next;
}

export function markDatasetRecent(id: number): RecentDataset[] {
  if (!isDatasetId(id)) return getRecentDatasets();
  const next = [{ id, at: new Date().toISOString() }, ...getRecentDatasets().filter((recent) => recent.id !== id)];
  return setRecentDatasets(next);
}

export function pruneDatasetPrefs(validDatasetIds: Iterable<number>): { favorites: number[]; recents: RecentDataset[] } {
  const validIds = new Set(Array.from(validDatasetIds).filter(isDatasetId));
  const favorites = getFavoriteDatasetIds();
  const recents = getRecentDatasets();

  const nextFavorites = favorites.filter((id) => validIds.has(id));
  const nextRecents = recents.filter((recent) => validIds.has(recent.id));

  if (nextFavorites.length !== favorites.length) setFavoriteDatasetIds(nextFavorites);
  if (nextRecents.length !== recents.length) setRecentDatasets(nextRecents);

  return { favorites: nextFavorites, recents: nextRecents };
}

export function getLandingPath(): LandingPath {
  const value = getPref<unknown>(PREF_KEYS.landing, "/");
  return isLandingPath(value) ? value : "/";
}

export function setLandingPath(path: LandingPath): void {
  setPref(PREF_KEYS.landing, path);
}

export function hasLandedThisSession(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return window.sessionStorage.getItem(SESSION_KEYS.landed) === "1";
  } catch {
    return memoryLanded;
  }
}

export function markLandedThisSession(): void {
  memoryLanded = true;
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(SESSION_KEYS.landed, "1");
  } catch {
    /* session storage unavailable: in-memory guard still applies */
  }
}

function emitPrefsChanged(key: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(PREF_EVENT, { detail: { key } }));
}

function normalizeIds(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<number>();
  const ids: number[] = [];
  for (const raw of value) {
    const id = Number(raw);
    if (!isDatasetId(id) || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  return ids;
}

function normalizeRecents(value: unknown): RecentDataset[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<number>();
  const recents: RecentDataset[] = [];

  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const record = item as Partial<RecentDataset>;
    const id = Number(record.id);
    if (!isDatasetId(id) || seen.has(id)) continue;

    seen.add(id);
    recents.push({
      id,
      at: typeof record.at === "string" && record.at ? record.at : new Date(0).toISOString(),
    });
  }

  return recents
    .sort((a, b) => timeValue(b.at) - timeValue(a.at))
    .slice(0, RECENT_LIMIT);
}

function isDatasetId(value: number): boolean {
  return Number.isInteger(value) && value > 0;
}

function timeValue(value: string): number {
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : 0;
}

function isLandingPath(value: unknown): value is LandingPath {
  return typeof value === "string" && (LANDING_PATHS as readonly string[]).includes(value);
}
