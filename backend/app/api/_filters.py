"""Shared helpers for building SQL filters out of user-supplied query params.

This is the import every ``?q=`` route uses::

    from app.api._filters import LIKE_ESCAPE, contains_pattern
    query.filter(Model.name.ilike(contains_pattern(q), escape=LIKE_ESCAPE))

The implementation itself lives one level up in :mod:`app.filters`, because the
same escaping is needed outside the HTTP layer (``core/contracts.py`` matches
contract clause markers built from a user-authored ``clause_id`` — #306) and
``app.core`` importing ``app.api`` for a pure string helper is the wrong
direction. This module is a re-export, not a second copy: there is exactly one
escaping implementation, and adding another anywhere is the bug this chokepoint
exists to prevent.
"""

from app.filters import LIKE_ESCAPE, contains_pattern, escape_like

__all__ = ["LIKE_ESCAPE", "contains_pattern", "escape_like"]
