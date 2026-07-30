"""The LIKE/ILIKE-escaping chokepoint — one implementation, importable anywhere.

Every LIKE/ILIKE pattern built from data the app did not author turns that data
into a *pattern*: ``%`` and ``_`` are wildcards, so an unescaped ``%`` matches
every row (an over-broad result nobody asked for, and on grant-scoped surfaces a
wider read than intended) and a pathological ``%_%_%_%_%_%`` makes the engine
backtrack hard (#282 / #273).

Usage — the escape function and the ESCAPE clause always travel together::

    from app.filters import LIKE_ESCAPE, contains_pattern
    query.filter(Model.name.ilike(contains_pattern(q), escape=LIKE_ESCAPE))

``ESCAPE`` is standard SQL and behaves identically on both engines we ship on:
SQLite has NO default escape character (so declaring one is *required* for the
escaping to mean anything), and PostgreSQL already defaults to a backslash (so
declaring it is a harmless no-op that keeps the two dialects aligned).

**Why this module and not ``app/api/_filters.py``** (which re-exports it, and is
still the import every ``?q=`` route uses): the same escaping is needed outside
the HTTP layer — ``core/contracts.py`` matches contract clause markers that
embed a user-authored ``clause_id`` (#306) — and ``core`` importing ``api`` to
get a pure string helper is the wrong direction. There is exactly one copy of
the escaping logic, and it lives here.
"""

__all__ = ["LIKE_ESCAPE", "contains_pattern", "escape_like"]

# The escape character itself, as a one-character Python string (a lone backslash).
LIKE_ESCAPE = "\\"


def escape_like(value: str) -> str:
    """Escape the LIKE/ILIKE metacharacters in user input so they match literally.

    The escape character must be doubled FIRST: doing it later would also escape
    the backslashes this function itself introduces.
    """
    return value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", r"\%").replace("_", r"\_")


def contains_pattern(value: str) -> str:
    """``%needle%`` with the needle's wildcards escaped — the shape every ``?q=``
    substring filter needs. Pair with ``escape=LIKE_ESCAPE`` at the call site."""
    return f"%{escape_like(value)}%"
