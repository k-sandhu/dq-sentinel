// Bridges the Workbench's introspected schema tree (GET /connections/{id}/schema)
// into CodeMirror's SQL completion config (#104), and maps our connection engine
// kinds (connectors/dialects.py registry) onto the closest CodeMirror SQL dialect.
// This is what makes table/column autocomplete *real* rather than typed-from-memory.

import { MSSQL, MySQL, PostgreSQL, SQLite, StandardSQL, sql } from "@codemirror/lang-sql";
import type { SQLDialect, SQLNamespace } from "@codemirror/lang-sql";
import type { Extension } from "@uiw/react-codemirror";
import type { SchemaTable } from "../api/types";

/** Closest CodeMirror SQL dialect for keyword highlighting + completion. The
 *  cloud engines (duckdb/snowflake/bigquery/trino/clickhouse) have no dedicated
 *  CodeMirror dialect, so they fall back to ANSI StandardSQL — a safe superset
 *  for completion (quoting itself is handled separately by lib/sqlIdent.ts, #83). */
export function dialectFor(kind: string | null | undefined): SQLDialect {
  switch (kind) {
    case "postgresql":
      return PostgreSQL;
    case "mysql":
      return MySQL;
    case "mssql":
      return MSSQL;
    case "sqlite":
      return SQLite;
    default:
      return StandardSQL;
  }
}

/** Build a CodeMirror completion schema from the schema tree. Tables are exposed
 *  both bare (`orders`) and nested under their schema (`public.orders`) so
 *  completion fires however the analyst qualifies the reference. Leaf arrays are
 *  the column names. */
export function buildSqlSchema(tables: readonly SchemaTable[]): SQLNamespace {
  const flat: Record<string, string[]> = {};
  const nested: Record<string, Record<string, string[]>> = {};
  for (const t of tables) {
    const columns = t.columns.map((c) => c.name);
    flat[t.table_name] = columns;
    if (t.schema_name) {
      (nested[t.schema_name] ??= {})[t.table_name] = columns;
    }
  }
  return { ...flat, ...nested } as SQLNamespace;
}

/** Assemble the `@codemirror/lang-sql` extension for a connection + schema. */
export function sqlExtension(
  kind: string | null | undefined,
  tables: readonly SchemaTable[],
): Extension {
  return sql({
    dialect: dialectFor(kind),
    schema: buildSqlSchema(tables),
    upperCaseKeywords: false,
  });
}
