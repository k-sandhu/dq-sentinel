// "Format SQL" for the Workbench (#104), dialect-aware via sql-formatter. The
// engine kind comes from the connection (connectors/dialects.py registry); we map
// it to the closest sql-formatter language. Formatting is a convenience, so invalid
// SQL is returned untouched rather than throwing in the editor.

import { format } from "sql-formatter";
import type { SqlLanguage } from "sql-formatter";

const LANGUAGE: Record<string, SqlLanguage> = {
  postgresql: "postgresql",
  duckdb: "postgresql", // DuckDB is PostgreSQL-flavored; closest supported grammar
  mysql: "mysql",
  mssql: "transactsql",
  sqlite: "sqlite",
  snowflake: "snowflake",
  bigquery: "bigquery",
  trino: "trino",
  // clickhouse has no dedicated grammar — generic "sql" handles it acceptably.
};

export function formatterLanguageFor(kind: string | null | undefined): SqlLanguage {
  return (kind && LANGUAGE[kind]) || "sql";
}

export function formatSql(value: string, kind: string | null | undefined): string {
  try {
    return format(value, {
      language: formatterLanguageFor(kind),
      keywordCase: "upper",
      tabWidth: 2,
    });
  } catch {
    return value; // leave un-parseable SQL exactly as typed
  }
}
