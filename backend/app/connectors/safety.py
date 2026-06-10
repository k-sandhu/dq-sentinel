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


class SqlNotAllowed(Exception):
    pass


def _strip_comments_and_strings(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)  # single-quoted literals
    return sql


def guard_sql(sql: str) -> str:
    """Validate and normalize a read-only query. Returns the cleaned SQL or raises."""
    if not sql or not sql.strip():
        raise SqlNotAllowed("Empty SQL")
    cleaned = sql.strip().rstrip(";").strip()
    stripped = _strip_comments_and_strings(cleaned)
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
