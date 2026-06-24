import { describe, expect, it } from "vitest";

import { fmtNum, fmtPct, fmtRelative, isRecent } from "./format";

describe("format helpers", () => {
  it("renders the null sentinel and percentages", () => {
    expect(fmtNum(null)).toBe("—");
    expect(fmtNum(undefined)).toBe("—");
    expect(fmtNum(5)).toBe("5");
    expect(fmtPct(null)).toBe("—");
    expect(fmtPct(0.941)).toBe("94.1%");
    expect(fmtPct(1)).toBe("100.0%");
  });

  // #66 honest-time rule: a bare ISO (no Z) must be treated as UTC, not local —
  // otherwise relative labels drift by the viewer's timezone offset.
  it("fmtRelative treats a bare (no-Z) ISO timestamp as UTC", () => {
    const ninetyMinAgo = new Date(Date.now() - 90 * 60 * 1000).toISOString().replace("Z", "");
    expect(fmtRelative(ninetyMinAgo)).toBe("1h ago");
    const fiveSecAgo = new Date(Date.now() - 5 * 1000).toISOString().replace("Z", "");
    expect(fmtRelative(fiveSecAgo)).toBe("now");
    expect(fmtRelative(null)).toBe("—");
  });

  it("isRecent uses a UTC-normalized 24h window", () => {
    const oneHourAgo = new Date(Date.now() - 3600 * 1000).toISOString().replace("Z", "");
    const twoDaysAgo = new Date(Date.now() - 48 * 3600 * 1000).toISOString().replace("Z", "");
    expect(isRecent(oneHourAgo)).toBe(true);
    expect(isRecent(twoDaysAgo)).toBe(false);
    expect(isRecent(null)).toBe(false);
  });
});
