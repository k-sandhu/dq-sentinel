// Typed localStorage JSON wrappers for small client-side preferences.
//
// Shared by the exceptions workspace (#63) and personalization (#59) — both
// issues spec this identical helper; whoever lands second reuses it. Keep the
// API (getPref/setPref/PREF_KEYS) stable so the merge is trivial.
//
// Saved-view/column stores are versioned (`_v1`) and shaped so a future
// server-side store (the enterprise multi-device follow-up) can ingest them
// unchanged — e.g. saved views are `{name, params}[]`, ready for a `user_views`
// table without reshaping.

export const PREF_KEYS = {
  views: "dq_views_v1", // SavedView[]
  cols: "dq_cols_v1", // string[] of hidden column ids (workspace table)
  landing: "dq_landing_v1", // default landing route (#59)
  favorites: "dq_favorites_v1", // favorite dataset ids (#59)
} as const;

export type PrefKey = (typeof PREF_KEYS)[keyof typeof PREF_KEYS];

/** A persisted saved view: a name + a URL search-param string. */
export interface SavedView {
  name: string;
  params: string;
}

/** Read a JSON pref, returning `fallback` on missing/corrupt/unavailable storage. */
export function getPref<T>(key: PrefKey, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

/** Write a JSON pref; swallows storage errors (private mode / quota). */
export function setPref<T>(key: PrefKey, value: T): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable — pref simply doesn't persist this session */
  }
}
