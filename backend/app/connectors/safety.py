"""SQL safety guard. EVERY query against a user's source database — whether written
by code, a user, or an LLM agent — must pass through guard_sql().

Defense layers:
1. connectors open sources read-only where the driver supports it, and switch off
   engine-level host access where the engine has such a switch (DuckDB:
   enable_external_access=false — see connectors/dialects.py);
2. guard_sql() allows a single SELECT/WITH statement, denylists side-effect keywords,
   denylists read-side functions that reach the filesystem/network/OS (#281), and
   rejects a bare string literal in table position (DuckDB replacement scan, #267);
3. callers wrap with enforce_limit() to bound result size.
"""

import re
from dataclasses import dataclass

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
        # `glob('/tmp/*')` lists host paths. SQLite's `x GLOB 'pat'` *operator* is not
        # a call, so pattern matching stays legal; only `glob(...)` is denied.
        "glob",
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

# A bare string literal in table position — `SELECT * FROM '/etc/passwd'` — is a
# DuckDB *replacement scan*: it reads the host filesystem with no function call at
# all, so _DENY_FUNCTION_CALL can never see it (#267). Masking literals to a space
# destroys the evidence too, so the check runs on a third mask variant in which each
# literal collapses to this sentinel instead. NUL does not occur in legitimate SQL, and
# smuggling one in can only cause an extra *rejection* here, never a bypass.
_LITERAL_MARK = "\x00"

# In that same variant a quoted identifier collapses to this sentinel rather than to a
# space, so `FROM "orders", '/etc/passwd'` still shows a countable table item before
# the comma. It is deliberately a *distinct* character used only here: the other
# variants keep masking identifiers to a space, which is what the keyword denylist,
# the `;` split and the column-list rules were written against.
_IDENTIFIER_MARK = "\x01"


# One bare-word run of the masked SQL: an identifier or number, with an optional
# trailing `&` so the `U&'...'` literal prefix stays a single run. The identifier
# sentinel is NOT in this class — it is a whole token on its own, and letting it join
# a run would turn `FROM"orders"` into the word `from<mark>` and lose the keyword.
_TOKEN_RUN = re.compile(r"[\w$.]+&?")

# Words after which a FROM/JOIN item list has ended, so a later comma belongs to some
# other list (`SELECT * FROM t ORDER BY from_date, 'x'`). `AS`, `ON`, `USING`, the
# join qualifiers and table modifiers are deliberately absent: they sit INSIDE a FROM
# clause, and `FROM a JOIN b ON a.k = b.k, '/etc/passwd'` is still a replacement scan.
_FROM_CLAUSE_ENDS: frozenset[str] = frozenset(
    {
        "select", "where", "group", "having", "by", "window", "qualify",
        "order", "limit", "offset", "fetch", "union", "intersect", "except", "minus",
    }
)

# Tokens a table item may directly follow: the keyword that opens the list, or the
# comma that separates it from the previous item.
_TABLE_ITEM_STARTS: frozenset[str] = frozenset({"from", "join", ","})

# The SQL-standard constructs that legitimately put FROM inside a function's argument
# list: EXTRACT(YEAR FROM '2024-01-01'), SUBSTRING(x FROM 'regex'),
# TRIM(BOTH FROM '  x  '), OVERLAY(a PLACING b FROM 2). A table reference never sits
# directly inside a function call, so these are the only exemptions needed.
_FROM_ARGUMENT_FUNCTIONS: frozenset[str] = frozenset({"extract", "substring", "trim", "overlay"})

# `name (a, b)` is a CTE / table-alias *column list*, not a call: `WITH url (id, addr)
# AS (...)`, `SELECT * FROM (SELECT 1) AS file (a)`. Legal analyst SQL even when the
# name collides with a denied function.
_COLUMN_LIST = re.compile(r"\s*[A-Za-z_][A-Za-z0-9_$]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_$]*)*\s*")
_AS_SUBQUERY = re.compile(r"\s*as\s*\(", re.IGNORECASE)
_DEFINITION_KEYWORDS = frozenset({"with", "recursive", "as"})
_BARE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


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


def _strip_comments_literals_and_identifiers(
    sql: str,
    *,
    keep_identifier_text: bool = False,
    literal_mark: str = " ",
    identifier_mark: str = " ",
) -> str:
    """Mask regions where semicolons and keywords are inert SQL text.

    With ``keep_identifier_text`` the *contents* of quoted identifiers survive (still
    without their quotes) — but only when the quoted name is a single bare word. That
    variant is only fed to the function denylist, so a quoted call like
    ``SELECT "pg_read_file"('/etc/passwd')`` cannot hide behind the mask, while a
    perfectly ordinary column named ``"URL (raw)"`` does not become a fake ``URL(``
    call. The keyword denylist keeps using the fully masked variant, where
    ``SELECT "delete" FROM t`` stays legal.

    ``literal_mark`` replaces each string / dollar-quoted literal and
    ``identifier_mark`` each masked quoted identifier. The default space erases them
    entirely; the table-position check passes sentinels so it can still see *that* a
    literal / an identifier was there without seeing its (attacker-controlled) contents.
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
            pieces.append(literal_mark)
            continue
        if ch in ('"', "`", "["):
            if ch == "[":
                end = _consume_bracket_identifier(sql, i)
            else:
                end = _consume_delimited(sql, i, ch, ch * 2)
            inner = sql[i + 1 : end - 1]
            # Only a single bare word can be a hidden function name; anything else
            # (`"URL (raw)"`, `[Cluster (k)]`) is just a column and must stay masked.
            keep = keep_identifier_text and _BARE_IDENTIFIER.fullmatch(inner) is not None
            pieces.append(inner if keep else identifier_mark)
            i = end
            continue
        if ch == "$":
            dollar_end = _consume_dollar_quoted_string(sql, i)
            if dollar_end is not None:
                i = dollar_end
                pieces.append(literal_mark)
                continue

        pieces.append(ch)
        i += 1
    return "".join(pieces)


def _word_ending_at(masked: str, end: int) -> str | None:
    """Lower-cased bare word that ends (ignoring trailing whitespace) at ``end``."""
    i = end
    while i > 0 and masked[i - 1].isspace():
        i -= 1
    stop = i
    while i > 0 and (masked[i - 1].isalnum() or masked[i - 1] in "_$"):
        i -= 1
    word = masked[i:stop]
    return word.lower() if word and not word[0].isdigit() else None


def _matching_paren(masked: str, open_index: int) -> int | None:
    depth = 0
    for i in range(open_index, len(masked)):
        if masked[i] == "(":
            depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


@dataclass
class _Frame:
    """Scanner state for one parenthesis depth."""

    call: str | None  # function whose argument list this is; None for a grouping paren
    in_from: bool = False  # currently inside a FROM/JOIN item list at this depth
    keyword: str = ""  # the FROM/JOIN that opened that list (for the message)
    prev: str = ""  # previous significant token, coarse-grained


def _literal_in_table_position(marked: str) -> str | None:
    """FROM/JOIN keyword whose item list holds a bare string literal, else ``None``.

    ONE left-to-right pass with a stack of frames — one per parenthesis depth, each
    carrying the enclosing call name and whether a FROM/JOIN item list is open at that
    depth. A literal is in table position when it is the token right after the
    keyword or right after a comma in that list, at the same depth: ``FROM '/etc/
    passwd'``, ``FROM "orders", '/etc/passwd'``, ``FROM (VALUES (1)) v, '/etc/
    passwd'``, ``FROM a JOIN b ON a.k = b.k, '/etc/passwd'``.

    Being a single pass (rather than rebuilding the paren stack per match) keeps
    guard_sql linear, and tracking depth explicitly means there is no cap on item
    count or on parenthesis nesting to walk past. ``EXTRACT(YEAR FROM 'x')`` and
    friends are exempt because the frame naming the call is right there.
    """
    frames = [_Frame(call=None)]
    i, n = 0, len(marked)
    while i < n:
        ch = marked[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            frames.append(_Frame(call=_word_ending_at(marked, i), prev="("))
            i += 1
            continue
        if ch == ")":
            if len(frames) > 1:
                frames.pop()
            frames[-1].prev = ")"
            i += 1
            continue
        frame = frames[-1]
        if ch == _IDENTIFIER_MARK:
            frame.prev = "word"  # a masked quoted identifier is a whole table item
            i += 1
            continue
        if ch == _LITERAL_MARK:
            if (
                frame.in_from
                and frame.prev in _TABLE_ITEM_STARTS
                and frame.call not in _FROM_ARGUMENT_FUNCTIONS
            ):
                return frame.keyword.upper()
            frame.prev = "literal"
            i += 1
            continue
        run = _TOKEN_RUN.match(marked, i)
        if run is None:
            frame.prev = "," if ch == "," else "operator"
            i += 1
            continue
        i = run.end()
        word = run.group(0).lower()
        if word in ("from", "join"):
            frame.in_from = True
            frame.keyword = word
            frame.prev = word
            continue
        if word in _FROM_CLAUSE_ENDS:
            frame.in_from = False
            frame.prev = "word"
            continue
        if i < n and marked[i] == _LITERAL_MARK:
            # E'' / N'' / X'' / U&'' / _utf8'' — a prefix flush against the literal,
            # not a token of its own, so it must not hide the comma before it.
            # Checked after the keywords: `FROM'/etc/passwd'` needs no whitespace.
            continue
        frame.prev = "word"
    return None


def _is_definition_column_list(masked: str, match: re.Match[str]) -> bool:
    """True when a ``name (...)`` match is a CTE / table-alias column list.

    ``WITH url (id, addr) AS (...)`` and ``... AS file (a)`` are legal even though the
    name collides with a denied function. The parenthesised part must be a plain list
    of bare identifiers (a real call's arguments are masked literals or expressions),
    *and* the construct must sit in a definition position.
    """
    open_index = match.end() - 1  # the regex ends on the "("
    close = _matching_paren(masked, open_index)
    if close is None or _COLUMN_LIST.fullmatch(masked, open_index + 1, close) is None:
        return False
    if _word_ending_at(masked, match.start()) in _DEFINITION_KEYWORDS:
        return True
    # Later CTEs in a list are preceded by a comma: `WITH a AS (...), url (id) AS (...)`.
    return _AS_SUBQUERY.match(masked, close + 1) is not None


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
        for call in _DENY_FUNCTION_CALL.finditer(candidate):
            if _is_definition_column_list(candidate, call):
                continue
            raise SqlNotAllowed(
                f"Function not allowed in read-only queries: {call.group(1).upper()}()"
            )
    # Engine-agnostic backstop for replacement scans (DuckDB reads a quoted path in
    # table position as a file). The engine-level switch in dialects.py is the primary
    # defense; this keeps the guard honest for any engine that grows the same feature.
    marked = _strip_comments_literals_and_identifiers(
        cleaned, literal_mark=_LITERAL_MARK, identifier_mark=_IDENTIFIER_MARK
    )
    keyword = _literal_in_table_position(marked)
    if keyword is not None:
        raise SqlNotAllowed(
            f"String literal not allowed in table position after {keyword}: "
            "some engines read it as a path to a file on the database host"
        )
    return cleaned


def enforce_limit(sql: str, limit: int) -> str:
    """Bound result size by wrapping the (already guarded) query in a LIMIT subquery."""
    return f"SELECT * FROM (\n{sql}\n) AS _dq_guard LIMIT {int(limit)}"
