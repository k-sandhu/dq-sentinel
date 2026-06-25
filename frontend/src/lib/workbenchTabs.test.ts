import { describe, expect, it } from "vitest";

import { deriveTabTitle } from "./workbenchTabs";

describe("deriveTabTitle", () => {
  it("uses the first non-empty, trimmed line of SQL", () => {
    expect(deriveTabTitle("\n\n  SELECT 1\nFROM t", 0)).toBe("SELECT 1");
  });

  it("falls back to a 1-based numbered title for empty/whitespace SQL", () => {
    expect(deriveTabTitle("", 0)).toBe("Query 1");
    expect(deriveTabTitle("   \n  ", 2)).toBe("Query 3");
  });

  it("truncates a long first line at 28 chars with an ellipsis", () => {
    const title = deriveTabTitle("SELECT a, b, c, d, e, f, g, h FROM really_wide_table", 0);
    expect(title.endsWith("…")).toBe(true);
    expect(title.length).toBe(29); // 28 chars + the ellipsis
  });
});
