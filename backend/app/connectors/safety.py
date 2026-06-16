"""SQL safety guard. EVERY query against a user's source database — whether written
by code, a user, or an LLM agent — must pass through guard_sql().

Defense layers:
1. connectors open sources read-only where the driver supports it;
2. guard_sql() allows a single SELECT/WITH statement and denylists side-effect keywords;
3. callers wrap with enforce_limit() to bound result size.
"""

import re

# Keywords that indicate writes/DDL/session changes. Checked on a copy of the SQL
# with string literals and comments stripped, so values like 'create' don't trip it.
_DENY = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|copy|merge|grant|revoke"
    r"|truncate|vacuum|pragma|call|exec|execute|reset|load|install|export|import|begin|commit|rollback)\b",
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


def _strip_comments_literals_and_identifiers(sql: str) -> str:
    """Mask regions where semicolons and keywords are inert SQL text."""
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
        if ch == '"':
            i = _consume_delimited(sql, i, '"', '""')
            pieces.append(" ")
            continue
        if ch == "`":
            i = _consume_delimited(sql, i, "`", "``")
            pieces.append(" ")
            continue
        if ch == "[":
            i = _consume_bracket_identifier(sql, i)
            pieces.append(" ")
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
    return cleaned


def enforce_limit(sql: str, limit: int) -> str:
    """Bound result size by wrapping the (already guarded) query in a LIMIT subquery."""
    return f"SELECT * FROM (\n{sql}\n) AS _dq_guard LIMIT {int(limit)}"
