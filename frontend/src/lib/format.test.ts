import { describe, expect, it } from "vitest";

import { fmtDate, fmtDateTime, fmtDuration, fmtNum, fmtPct, fmtRelative, isRecent } from "./format";

/** The viewer's short zone name, resolved the same way `fmtDateTime` does, so the
 *  assertions below hold under any TZ the test runner happens to use. */
function shortZone(d: Date): string {
  const part = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
    .formatToParts(d)
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? "";
}

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

  // #294: an unlabelled local-time stamp is mis-read by every reader whose offset
  // isn't zero, and a year-less "Jan 4" reads as *this* January.
  describe("fmtDateTime", () => {
    it("names the viewer's timezone", () => {
      const iso = `${new Date().getUTCFullYear()}-06-15T12:00:00`;
      const zone = shortZone(new Date(`${iso}Z`));
      expect(zone).not.toBe("");
      expect(fmtDateTime(iso)).toContain(zone);
    });

    it("omits the year for the current year and includes it otherwise", () => {
      const year = new Date().getUTCFullYear();
      expect(fmtDateTime(`${year}-06-15T12:00:00`)).not.toContain(String(year));
      expect(fmtDateTime("2019-06-15T12:00:00")).toContain("2019");
    });

    it("treats a bare (no-Z) ISO as UTC and guards null/garbage", () => {
      // Same instant, one spelled with Z and one without — must render identically.
      expect(fmtDateTime("2019-06-15T12:00:00")).toBe(fmtDateTime("2019-06-15T12:00:00Z"));
      expect(fmtDateTime(null)).toBe("—");
      expect(fmtDateTime("not-a-date")).toBe("—");
    });
  });

  it("fmtDate renders a UTC-normalized day and keeps the year", () => {
    expect(fmtDate("2019-06-15T12:00:00")).toContain("2019");
    expect(fmtDate("2019-06-15T12:00:00")).toBe(fmtDate("2019-06-15T12:00:00Z"));
    expect(fmtDate(null)).toBe("—");
    expect(fmtDate("nope")).toBe("—");
  });

  it("fmtDuration steps through seconds/minutes/hours/days", () => {
    expect(fmtDuration(null)).toBe("—");
    expect(fmtDuration(undefined)).toBe("—");
    expect(fmtDuration(45)).toBe("45s");
    expect(fmtDuration(600)).toBe("10m");
    expect(fmtDuration(5040)).toBe("1.4h");
    expect(fmtDuration(181_440)).toBe("2.1d");
  });

  it("isRecent uses a UTC-normalized 24h window", () => {
    const oneHourAgo = new Date(Date.now() - 3600 * 1000).toISOString().replace("Z", "");
    const twoDaysAgo = new Date(Date.now() - 48 * 3600 * 1000).toISOString().replace("Z", "");
    expect(isRecent(oneHourAgo)).toBe(true);
    expect(isRecent(twoDaysAgo)).toBe(false);
    expect(isRecent(null)).toBe(false);
  });
});
