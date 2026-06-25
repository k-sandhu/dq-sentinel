import { describe, expect, it } from "vitest";

import { csvSafeCell, rowsToCsv } from "./csv";

describe("csvSafeCell", () => {
  // Mirrors the backend _csv_safe() guard (#57): a leading =,+,-,@,TAB,CR is
  // prefixed with a quote so Excel/Sheets can't evaluate it as a formula.
  it("neutralizes spreadsheet formula-injection prefixes", () => {
    for (const p of ["=", "+", "-", "@", "\t", "\r"]) {
      expect(csvSafeCell(`${p}cmd`)).toBe(`'${p}cmd`);
    }
  });

  it("leaves safe values untouched and renders null/undefined as empty", () => {
    expect(csvSafeCell("orders")).toBe("orders");
    expect(csvSafeCell(42)).toBe("42");
    expect(csvSafeCell(null)).toBe("");
    expect(csvSafeCell(undefined)).toBe("");
  });

  it("serializes objects via JSON before guarding", () => {
    expect(csvSafeCell({ a: 1 })).toBe('{"a":1}');
  });
});

describe("rowsToCsv", () => {
  it("RFC-4180 quotes fields with commas/quotes and doubles embedded quotes", () => {
    expect(rowsToCsv(["name", "note"], [["a,b", 'say "hi"']])).toBe(
      'name,note\r\n"a,b","say ""hi"""\r\n',
    );
  });

  it("emits a header-only line for an empty result", () => {
    expect(rowsToCsv(["id"], [])).toBe("id\r\n");
  });

  it("applies the formula-injection guard inside the CSV body", () => {
    expect(rowsToCsv(["f"], [["=1+1"]])).toBe("f\r\n'=1+1\r\n");
  });
});
