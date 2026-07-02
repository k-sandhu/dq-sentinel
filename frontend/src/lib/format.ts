export function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString();
}

export function fmtPct(ratio: number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
  return String(v);
}

/** Parse an API timestamp as UTC. The backend stores naive-UTC and serializes
 *  without an offset, so an ISO string lacking `Z`/`+hh:mm` must be treated as
 *  UTC, not local. Single source of truth for all the relative/absolute helpers. */
function parseUtc(iso: string): Date {
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = parseUtc(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const d = parseUtc(iso);
  const secs = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

/** Compact relative time for tables ("3h ago", "5m ago", "2d ago"). Used by the
 *  exceptions workspace "last seen" column (#63). UTC-normalized like the rest. */
export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = parseUtc(iso);
  const secs = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (secs < 45) return "now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 86400 * 30) return `${Math.floor(secs / 86400)}d ago`;
  return `${Math.floor(secs / (86400 * 30))}mo ago`;
}

/** True when an ISO timestamp is within the last 24h (the "new" tint, #63). */
export function isRecent(iso: string | null | undefined, hours = 24): boolean {
  if (!iso) return false;
  return Date.now() - parseUtc(iso).getTime() < hours * 3600 * 1000;
}

export function describeSchedule(kind: string | null, expr: string | null): string {
  if (!kind || !expr) return "manual";
  if (kind === "cron") return `cron ${expr}`;
  const mins = parseInt(expr, 10);
  if (Number.isNaN(mins)) return expr;
  if (mins % 1440 === 0) return mins === 1440 ? "daily" : `every ${mins / 1440}d`;
  if (mins % 60 === 0) return mins === 60 ? "hourly" : `every ${mins / 60}h`;
  return `every ${mins}m`;
}
