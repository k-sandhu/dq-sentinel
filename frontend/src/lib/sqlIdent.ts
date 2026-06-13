// Dialect-aware SQL identifier quoting for the Workbench (issue #83 / BF-9).
//
// The backend quotes via SQLAlchemy's `identifier_preparer.quote()`
// (see backend/app/connectors/sa.py: Connector.quote / table_ref). We can't run
// that in the browser, so this mirrors its behaviour closely enough to emit SQL
// that is safe to paste back to the same source:
//   * pick the quote character from the connection's engine kind (Connection.kind,
//     which is one of the dialects.py registry kinds), and
//   * only quote when an identifier actually needs it (reserved word, mixed/upper
//     case, leading digit, or any character outside a plain lower-snake token),
//     escaping an embedded closing quote by doubling it.
// Quoting a plain identifier is always semantically safe, so when in doubt we
// quote — the failure mode we must avoid is leaving a name that needs quoting bare.

/** Connection engine kinds (mirrors backend/app/connectors/dialects.py REGISTRY). */
export type DialectKind =
  | "sqlite"
  | "duckdb"
  | "postgresql"
  | "mysql"
  | "mssql"
  | "snowflake"
  | "bigquery"
  | "trino"
  | "clickhouse";

interface QuotePair {
  open: string;
  close: string;
}

// ANSI double quotes are the default; MySQL/MariaDB and BigQuery use backticks;
// SQL Server uses square brackets. Anything unknown falls back to ANSI quotes.
const ANSI: QuotePair = { open: '"', close: '"' };
const QUOTES: Partial<Record<DialectKind, QuotePair>> = {
  mysql: { open: "`", close: "`" },
  bigquery: { open: "`", close: "`" },
  mssql: { open: "[", close: "]" },
};

export function quoteChars(kind: string | null | undefined): QuotePair {
  if (!kind) return ANSI;
  return QUOTES[kind as DialectKind] ?? ANSI;
}

// Reserved words that collide with all-lowercase identifiers. Structural cases
// (mixed case, special chars, leading digit) are handled separately and don't
// depend on this set, so it only needs to cover bare lower-case keyword names.
// Superset of the words SQLAlchemy reserves across SQLite/PostgreSQL/MySQL/
// SQL Server, plus common cloud-warehouse keywords (Snowflake/BigQuery/Trino/
// ClickHouse). Over-inclusion is harmless — it just adds quotes.
const RESERVED = new Set<string>([
  "all", "and", "any", "array", "as", "asc", "between", "by", "case", "cast",
  "check", "collate", "column", "constraint", "create", "cross", "cube",
  "current", "current_date", "current_role", "current_time", "current_timestamp",
  "current_user", "default", "delete", "desc", "describe", "distinct", "drop",
  "else", "end", "escape", "except", "exists", "false", "fetch", "filter", "first",
  "following", "for", "foreign", "from", "full", "grant", "group", "grouping",
  "having", "ilike", "in", "inner", "insert", "intersect", "interval", "into", "is",
  "join", "lateral", "left", "like", "limit", "natural", "not", "null", "nulls",
  "of", "offset", "on", "or", "order", "outer", "over", "partition", "pivot",
  "preceding", "primary", "qualify", "range", "references", "right", "rollup",
  "row", "rows", "select", "set", "some", "table", "then", "to", "true", "unbounded",
  "union", "unique", "unpivot", "update", "user", "using", "values", "when",
  "where", "window", "with",
]);

/** True when `name` cannot be left unquoted for this dialect. */
function requiresQuotes(name: string): boolean {
  if (name.length === 0) return true;
  // A "plain" identifier is a lower-case snake token starting with a letter or
  // underscore. Anything else (upper/mixed case, leading digit, dots, spaces,
  // quotes, hyphens, unicode, …) must be quoted to round-trip correctly.
  if (!/^[a-z_][a-z0-9_]*$/.test(name)) return true;
  return RESERVED.has(name);
}

/** Quote a single identifier for the given dialect, only when necessary. */
export function quoteIdent(name: string, kind: string | null | undefined): string {
  const { open, close } = quoteChars(kind);
  if (!requiresQuotes(name)) return name;
  // Escape the closing delimiter by doubling it (ANSI "" / MySQL `` / MSSQL ]]).
  return open + name.split(close).join(close + close) + close;
}

/**
 * A schema-qualified, dialect-quoted table reference: `schema.table`, or just
 * `table` when no schema is known. Mirrors Connector.table_ref().
 */
export function qualifiedRef(
  schema: string | null | undefined,
  table: string,
  kind: string | null | undefined,
): string {
  const t = quoteIdent(table, kind);
  return schema ? `${quoteIdent(schema, kind)}.${t}` : t;
}
