import { describe, expect, it, vi } from "vitest";

import {
  getFavorites,
  getRawPref,
  getRecents,
  pruneStalePrefs,
  pushRecent,
  setRawPref,
  subscribePrefs,
  toggleFavorite,
} from "./prefs";

describe("prefs chokepoint", () => {
  it("toggleFavorite adds to the front and removes on a second toggle", () => {
    expect(toggleFavorite(7)).toBe(true);
    expect(toggleFavorite(9)).toBe(true);
    expect(getFavorites()).toEqual([9, 7]); // most-recently-starred first
    expect(toggleFavorite(7)).toBe(false);
    expect(getFavorites()).toEqual([9]);
  });

  it("pushRecent dedupes (most-recent-first) and never duplicates", () => {
    for (const id of [1, 2, 3, 2, 1]) pushRecent(id);
    const ids = getRecents().map((r) => r.id);
    expect(ids[0]).toBe(1);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("setRawPref writes/removes a value and fires the dq:prefs event", () => {
    const handler = vi.fn();
    const unsub = subscribePrefs(handler);
    setRawPref("dq-dir", "graphite");
    expect(getRawPref("dq-dir")).toBe("graphite");
    expect(handler).toHaveBeenCalledTimes(1);
    setRawPref("dq-dir", null);
    expect(getRawPref("dq-dir")).toBeNull();
    expect(handler).toHaveBeenCalledTimes(2);
    unsub();
  });

  it("pruneStalePrefs writes nothing and fires no event when nothing is stale", () => {
    toggleFavorite(5);
    const handler = vi.fn();
    const unsub = subscribePrefs(handler);
    pruneStalePrefs([5]); // 5 is still live -> no write, no spurious dq:prefs
    expect(handler).not.toHaveBeenCalled();
    expect(getFavorites()).toEqual([5]);
    unsub();
  });
});
