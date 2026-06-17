// Client-side CSV / JSON / TSV serialization + download for the Workbench result
// grid (#104). Exports happen entirely in the browser — no server round-trip — so
// the formula-injection defense lives here, mirroring the server-side exceptions
// export (backend/app/api/exceptions_api.py:_csv_safe, #57): a leading
// =,+,-,@,TAB,CR is prefixed with a single quote before Excel/Sheets can evaluate
// it. Keeping the two implementations identical means an exported result CSV is as
// safe to open as an exported exceptions CSV.

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const DANGEROUS_PREFIX = new Set(["=", "+", "-", "@", "\t", "\r"]);

/** Neutralize spreadsheet formula injection — mirrors backend `_csv_safe()` (#57). */
export function csvSafeCell(value: unknown): string {
  const s = cellText(value);
  return s.length > 0 && DANGEROUS_PREFIX.has(s[0]) ? `'${s}` : s;
}

/** RFC-4180 quoting: wrap in double quotes (doubling embedded quotes) when the
 *  field contains a delimiter, quote, or newline. */
function csvQuote(s: string): string {
  return /[",\r\n]/.test(s) ? `"${s.split('"').join('""')}"` : s;
}

export function rowsToCsv(columns: string[], rows: readonly unknown[][]): string {
  const line = (cells: readonly unknown[]) =>
    cells.map((c) => csvQuote(csvSafeCell(c))).join(",");
  const header = line(columns);
  if (rows.length === 0) return `${header}\r\n`;
  return `${header}\r\n${rows.map(line).join("\r\n")}\r\n`;
}

export function rowsToJson(columns: string[], rows: readonly unknown[][]): string {
  const objects = rows.map((r) =>
    Object.fromEntries(columns.map((c, i) => [c, r[i] ?? null])),
  );
  return JSON.stringify(objects, null, 2);
}

/** Tab-separated text for clipboard copy (pastes cleanly into Excel/Sheets).
 *  Tabs/newlines inside values are flattened to spaces so the grid shape holds. */
export function rowsToTsv(columns: string[], rows: readonly unknown[][]): string {
  const clean = (v: unknown) => cellText(v).replace(/[\t\r\n]+/g, " ");
  const line = (cells: readonly unknown[]) => cells.map(clean).join("\t");
  return [line(columns), ...rows.map(line)].join("\n");
}

/** Trigger a client-side file download (same anchor pattern as api.ts:download). */
export function downloadText(filename: string, text: string, mime: string): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  try {
    document.body.appendChild(a);
    a.click();
  } finally {
    a.remove();
    URL.revokeObjectURL(url);
  }
}
