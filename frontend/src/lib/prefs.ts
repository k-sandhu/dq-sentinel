// Typed localStorage wrappers for small per-user UI preferences.
//
// NOTE (merge coordination): this file is also created by the personalization
// issues (#59 / PR #86 and #63). The shape below is the documented shared
// contract — if a sibling's identical file lands first, this is a trivial merge.
// Extend PREF_KEYS *additively*; never redefine an existing key.

export const PREF_KEYS = {
  /** Path the app navigates to as the user's landing page, e.g. "/dashboards/3". */
  landing: "dq.pref.landing",
} as const;

export type PrefKey = (typeof PREF_KEYS)[keyof typeof PREF_KEYS];

export function getPref(key: PrefKey): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // storage unavailable (private mode / disabled) — degrade quietly
  }
}

export function setPref(key: PrefKey, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable — preference simply doesn't persist */
  }
}
