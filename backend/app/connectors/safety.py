"""SQL safety guard. EVERY query against a user's source database — whether written
by code, a user, or an LLM agent — must pass through guard_sql().

Defense layers:
1. connectors open sources read-only where the driver supports it;
2. guard_sql() allows a single SELECT/WITH statement, denylists side-effect keywords,
   and denylists read-side functions that reach the filesystem/network/OS (#281);
3. callers wrap with enforce_limit() to bound result size.
"""

import re

# Keywords that indicate writes/DDL/session changes. Checked on a masked copy of the
# SQL with comments, string/dollar-quoted literals, and quoted identifiers hidden,
# so inert values like 'create' and identifiers like "delete" don't trip it.
_DENY = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|copy|merge|grant|revoke"
    r"|truncate|vacuum|pragma|call|exec|execute|reset|load|install|export|import|begin|commit|rollback)\b",
    re.IGNORECASE,
)

# Read-side functions that escape the database: host-file reads, outbound network
# calls, cross-engine federation, and OS command execution. A `SELECT read_text(...)`
# is still a single SELECT with no write keyword, so the denylist above never sees it
# (#281; the DuckDB host-file read is #267).
#
# These are matched as *calls* — the name followed by optional whitespace then "(" —
# so a column or table named `url`, `file`, `files` or `text` remains legal.
# Schema-qualified forms (`pg_catalog.pg_read_file(...)`, `sys.xp_cmdshell(...)`) are
# covered because the leading word boundary sits on the dot.
_DENY_FUNCTIONS: frozenset[str] = frozenset(
    {
        # DuckDB: file / HTTP / object-store table functions and scanners.
        "read_text",
        "read_blob",
        "read_csv",
        "read_csv_auto",
        "sniff_csv",
        "read_json",
        "read_json_auto",
        "read_json_objects",
        "read_json_objects_auto",
        "read_ndjson",
        "read_ndjson_auto",
        "read_ndjson_objects",
        "read_parquet",
        "read_avro",
        "read_arrow",
        "read_xlsx",
        "parquet_scan",
        "parquet_metadata",
        "parquet_file_metadata",
        "parquet_kv_metadata",
        "parquet_schema",
        "delta_scan",
        "iceberg_scan",
        "iceberg_metadata",
        "iceberg_snapshots",
        "st_read",
        "st_readosm",
        # DuckDB: federation into other engines.
        "postgres_scan",
        "postgres_scan_pushdown",
        "postgres_query",
        "mysql_scan",
        "mysql_query",
        "sqlite_scan",
        "sqlite_query",
        # MySQL / MariaDB.
        "load_file",
        "sys_exec",
        "sys_eval",
        # ClickHouse table functions (file, object store, network, other engines).
        "url",
        "urlcluster",
        "s3",
        "s3cluster",
        "gcs",
        "azureblobstorage",
        "file",
        "filecluster",
        "hdfs",
        "hdfscluster",
        "deltalake",
        "iceberg",
        "hudi",
        "remote",
        "remotesecure",
        "cluster",
        "clusterallreplicas",
        "mysql",
        "postgresql",
        "sqlite",
        "mongodb",
        "redis",
        "jdbc",
        "odbc",
        "executable",
        # PostgreSQL: server-side file/dir reads, large objects, federation,
        # and the query_to_xml family (which executes an arbitrary SQL string).
        "pg_read_file",
        "pg_read_binary_file",
        "pg_stat_file",
        "pg_ls_dir",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_ls_tmpdir",
        "pg_ls_archive_statusdir",
        "pg_file_read",
        "pg_file_write",
        "pg_file_unlink",
        "pg_logdir_ls",
        "lo_import",
        "lo_export",
        "lo_get",
        "lo_put",
        "dblink",
        "dblink_connect",
        "dblink_connect_u",
        "dblink_exec",
        "dblink_open",
        "dblink_fetch",
        "dblink_send_query",
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        # SQLite: fileio / zipfile extension functions and extension loading.
        "readfile",
        "writefile",
        "fsdir",
        "lsmode",
        "zipfile",
        "load_extension",
        # SQL Server: ad-hoc remote/bulk sources and the extended-procedure surface.
        "openrowset",
        "opendatasource",
        "openquery",
        "openxml",
        "xp_cmdshell",
        "xp_dirtree",
        "xp_fileexist",
        "xp_subdirs",
        "xp_regread",
        "sp_oacreate",
        "sp_oamethod",
        # Snowflake / BigQuery: staged-file inspection and federated queries.
        "infer_schema",
        "get_presigned_url",
        "external_query",
    }
)

# Longest name first so the error message names the most specific match
# (`READ_CSV_AUTO()` rather than `READ_CSV()`).
_DENY_FUNCTION_CALL = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in sorted(_DENY_FUNCTIONS, key=len, reverse=True)) + r")\s*\(",
    re.IGNORECASE,
)

_STARTS_OK = re.compile(r"^(select|with)\b", re.IGNORECASE)
_DOLLAR_QUOTE_START = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


class SqlNotAllowed(ValueError):
    """Raised for non-SELECT / multi-statement / denylisted SQL. Subclasses
    ValueError so API validation paths (`except ValueError -> 422`) catch it."""


def _consume_delimited(sql: str, start: int, delimiter: str, escaped_delimiter: str) -> int:
    i = start + len(delimiter)
    while i < len(sql):
        if sql.startswith(escaped_delimiter, i):
            i += len(escaped_delimiter)
            continue
        if sql.startswith(delimiter, i):
            return i + len(delimiter)
        i += 1
    raise SqlNotAllowed("Unterminated SQL literal or quoted identifier")


def _consume_bracket_identifier(sql: str, start: int) -> int:
    i = start + 1
    while i < len(sql):
        if sql.startswith("]]", i):
            i += 2
            continue
        if sql[i] == "]":
            return i + 1
        i += 1
    raise SqlNotAllowed("Unterminated SQL literal or quoted identifier")


def _consume_dollar_quoted_string(sql: str, start: int) -> int | None:
    match = _DOLLAR_QUOTE_START.match(sql, start)
    if not match:
        return None
    delimiter = match.group(0)
    end = sql.find(delimiter, match.end())
    if end == -1:
        raise SqlNotAllowed("Unterminated SQL literal or quoted identifier")
    return end + len(delimiter)


def _strip_comments_literals_and_identifiers(sql: str, *, keep_identifier_text: bool = False) -> str:
    """Mask regions where semicolons and keywords are inert SQL text.

    With ``keep_identifier_text`` the *contents* of quoted identifiers survive (still
    without their quotes). That variant is only fed to the function denylist, so a
    quoted call like ``SELECT "pg_read_file"('/etc/passwd')`` cannot hide behind the
    mask — the keyword denylist keeps using the fully masked variant, where
    ``SELECT "delete" FROM t`` stays legal.
    """
    pieces: list[str] = []
    i = 0
    while i < len(sql):
        if sql.startswith("--", i):
            end = sql.find("\n", i + 2)
            if end == -1:
                pieces.append(" ")
                break
            pieces.append(" ")
            i = end
            continue

        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end == -1:
                raise SqlNotAllowed("Unterminated block comment")
            pieces.append(" ")
            i = end + 2
            continue

        ch = sql[i]
        if ch == "'":
            i = _consume_delimited(sql, i, "'", "''")
            pieces.append(" ")
            continue
        if ch in ('"', "`", "["):
            if ch == "[":
                end = _consume_bracket_identifier(sql, i)
            else:
                end = _consume_delimited(sql, i, ch, ch * 2)
            pieces.append(sql[i + 1 : end - 1] if keep_identifier_text else " ")
            i = end
            continue
        if ch == "$":
            dollar_end = _consume_dollar_quoted_string(sql, i)
            if dollar_end is not None:
                i = dollar_end
                pieces.append(" ")
                continue

        pieces.append(ch)
        i += 1
    return "".join(pieces)


def guard_sql(sql: str) -> str:
    """Validate and normalize a read-only query. Returns the cleaned SQL or raises."""
    if not sql or not sql.strip():
        raise SqlNotAllowed("Empty SQL")
    cleaned = sql.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    stripped = _strip_comments_literals_and_identifiers(cleaned)
    if ";" in stripped:
        raise SqlNotAllowed("Multiple statements are not allowed")
    if not _STARTS_OK.match(stripped.lstrip()):
        raise SqlNotAllowed("Only SELECT / WITH queries are allowed")
    match = _DENY.search(stripped)
    if match:
        raise SqlNotAllowed(f"Keyword not allowed in read-only queries: {match.group(1).upper()}")
    # Comments and string literals are masked in both variants, so a denylisted name
    # mentioned inside a literal or a comment is not a match.
    unquoted = _strip_comments_literals_and_identifiers(cleaned, keep_identifier_text=True)
    for candidate in (stripped, unquoted):
        call = _DENY_FUNCTION_CALL.search(candidate)
        if call:
            raise SqlNotAllowed(
                f"Function not allowed in read-only queries: {call.group(1).upper()}()"
            )
    return cleaned


def enforce_limit(sql: str, limit: int) -> str:
    """Bound result size by wrapping the (already guarded) query in a LIMIT subquery."""
    return f"SELECT * FROM (\n{sql}\n) AS _dq_guard LIMIT {int(limit)}"
