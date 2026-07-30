"""The source-error redaction chokepoint (#307).

Driver and SQLAlchemy exception text routinely carries the *infrastructure* of a
source, not just the failure::

    connection to server at "db.internal" (10.0.0.5), port 5432 failed:
    FATAL: password authentication failed for user "svc_dq"

Echoing that into an API response puts hostnames, ports and service accounts in
front of every analyst who can see the connection — and into their browser, into
screenshots, and into whatever downstream log the UI error box ends up in. It
also leaks the shape of infrastructure the caller may never be granted.

Silence is not the answer either: a source error the analyst cannot act on is a
support ticket. So ``redact_source_text`` runs three steps, in order:

1. **Drop the appendix.** SQLAlchemy's ``[SQL: ...]`` / ``[parameters: ...]``
   tail and its ``(Background on this error at: ...)`` footer go first — the
   workbench complaint in #307 was ``/query/run`` echoing back the *rewritten
   guard SQL*. Doing this before step 2 also stops a fragment of the analyst's
   own SQL from being mistaken for a driver phrase.
2. **Classify — the primary control.** A curated table maps well-known driver
   phrasings onto a fixed, safe sentence ("authentication with the source
   failed", "the source host could not be resolved", ...). Nothing from the
   driver text is echoed: the returned string is a constant defined in this file.
   This is what every connectivity path relies on.
3. **Scrub — a bounded backstop.** Anything unclassified keeps its wording (that
   is the point: ``no such column: emial`` must survive) with the constructs that
   can carry infrastructure removed — DSNs, ``host=``/``port=``/``user=`` pairs,
   ``host "x"`` / ``server at "x"`` forms, IP literals and ``host:port`` — then
   collapsed to one bounded line.

Step 3 is regex over text, not a parser, exactly like ``connectors/safety.py``:
treat it as defence in depth, never as the thing standing between a source and
the client. Known limit: a *bare* hostname with no label, no port and no scheme
(``db.internal`` on its own) is indistinguishable from a qualified table name
(``public.orders``) and is left alone — redacting it would gut the debuggability
this module is trying to preserve. If a driver phrasing shows up that carries
infrastructure and is not classified, add it to ``_REASONS``; that is the fix,
not a wider regex.

The FULL exception always goes to the structured server log under the request
id — use ``redact_source_error``, which logs it for you.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

#: Keep a source error readable in a UI error box; the full text stays in the log.
MAX_CHARS = 300

#: What a value is replaced with once scrubbed.
TOKEN = "[redacted]"

#: Last resort when nothing readable survives redaction.
_UNRECOGNIZED = "the source driver reported an error (see the server log for this request id)"


# ---------------------------------------------------------------- (1) appendix
# SQLAlchemy StatementError renders "<msg>\n[SQL: ...]\n[parameters: ...]".
# Anchored on SQLAlchemy's punctuation, not a bare word boundary: pyodbc injects
# a literal "[SQL Server]" tag into every MSSQL error, and `\[SQL\b` swallowed it
# plus the entire diagnosis after it — which also deleted "Login failed for user"
# before the classifier below ever saw it. SQLAlchemy always renders the colon.
_APPENDIX = re.compile(r"\[(?:SQL:|parameters:|cached since\s).*", re.S | re.I)
_BACKGROUND = re.compile(r"\(Background on this error at:[^)]*\)?", re.I)
# SQLAlchemy prefixes the DBAPI class, e.g. "(psycopg2.OperationalError) ...".
_DBAPI_PREFIX = re.compile(r"^\((?:[\w.]+\.)?\w*(?:Error|Exception|Warning)\)\s*", re.I)


# ------------------------------------------------------------- (2) classifiers
# Ordered: the more specific cause wins. Each entry maps *many* driver phrasings
# (postgres, mysql, mssql, sqlite, duckdb, snowflake, trino) onto ONE safe line.
_REASONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"password authentication failed|authentication failed|access denied for user"
            r"|login failed for user|auth(?:entication)? (?:error|failure)"
            r"|incorrect username or password|invalid (?:credential|username|password)",
            re.I,
        ),
        "authentication with the source failed",
    ),
    (
        re.compile(
            r"could not translate host name|name or service not known|nodename nor servname"
            r"|getaddrinfo failed|temporary failure in name resolution|no such host is known"
            r"|unknown (?:host|server host)",
            re.I,
        ),
        "the source host could not be resolved",
    ),
    (
        re.compile(
            r"connection refused|could not connect to server|actively refused it"
            r"|no route to host|network is unreachable|is the server running",
            re.I,
        ),
        "the source refused the connection",
    ),
    (
        re.compile(r"timeout expired|timed out|connection timeout|read timeout", re.I),
        "the source did not respond in time",
    ),
    (
        re.compile(
            r"ssl error|ssl syscall|ssl routines|ssl connection has been closed"
            r"|certificate verify failed|tlsv1|sslv3",
            re.I,
        ),
        "the encrypted connection to the source could not be established",
    ),
    (
        re.compile(
            r"database \S+ does not exist|unknown database|catalog \S+ does not exist"
            r"|cannot open database \S+ requested by the login",
            re.I,
        ),
        "the target database does not exist on the source",
    ),
    (
        re.compile(
            r"unable to open database file|no such file or directory|file is not a database"
            r"|database is locked|not a valid duckdb database",
            re.I,
        ),
        "the source database file could not be opened",
    ),
    (
        re.compile(
            # No bare "not authorized": Snowflake deliberately says
            # "Object 'X' does not exist or not authorized." for a plain typo so
            # it does not disclose existence, and classifying that as a grant
            # failure sends the analyst hunting a permission they already have.
            # A genuine Snowflake grant failure says "Insufficient privileges".
            r"permission denied for|insufficient privilege"
            r"|does not have (?:the )?permission",
            re.I,
        ),
        "the source account is not permitted to read this object",
    ),
)


# ----------------------------------------------------------------- (3) scrubs
# key=value -> key=[redacted]; the KEY is kept because "host=[redacted] failed"
# still tells an operator which knob to look at.
_KEYED = re.compile(
    r"\b(host|hostaddr|hostname|server|servername|port|user|username|uid|login|password|pwd"
    r"|passwd|account|dsn|endpoint|address|addr)(\s*=\s*)(\"[^\"]*\"|'[^']*'|[^\s,;)\]]+)",
    re.I,
)
# "host name \"db.internal\"", "for user \"svc_dq\"", "port 5432"
_LABELLED = re.compile(
    r"\b(host(?:\s*name)?|hostname|server|user|username|account|login|role|port|address)(\s+)"
    r"(\"[^\"]*\"|'[^']*'|`[^`]*`|\d{1,5})",
    re.I,
)
# postgres: connection to server at "db.internal" (10.0.0.5), port 5432 failed
_SERVER_AT = re.compile(r"\b(server|host)(\s+(?:at|on)\s+)(\"[^\"]*\"|'[^']*')", re.I)
_DSN = re.compile(r"\b[a-z][a-z0-9+.\-]*://\S*", re.I)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# 4+ colon-separated hextets, so "12:34:56" in a timestamp is not an IPv6 address.
_IPV6 = re.compile(r"\b(?:[0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}\b|::[0-9a-f]{1,4}\b", re.I)
_HOSTPORT = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+[a-z]{2,}:\d{1,5}\b", re.I)
_REPEATED_TOKEN = re.compile(r"(?:" + re.escape(TOKEN) + r"[\s,]*){2,}")
#: ``Connector.test()`` renders its own "Connection failed: " lead-in; the call
#: sites below add their own sentence, so drop it rather than stack the two.
_PROBE_PREFIX = re.compile(r"^\s*connection failed:\s*", re.I)


def _strip_appendix(text: str) -> str:
    out = _APPENDIX.sub("", text)
    out = _BACKGROUND.sub("", out)
    out = _DBAPI_PREFIX.sub("", out.strip())
    return " ".join(out.split())


def _scrub(text: str) -> str:
    out = _DSN.sub(TOKEN, text)
    out = _KEYED.sub(lambda m: f"{m.group(1)}={TOKEN}", out)
    out = _SERVER_AT.sub(lambda m: f"{m.group(1)}{m.group(2)}{TOKEN}", out)
    out = _LABELLED.sub(lambda m: f"{m.group(1)}{m.group(2)}{TOKEN}", out)
    out = _HOSTPORT.sub(TOKEN, out)
    out = _IPV4.sub(TOKEN, out)
    out = _IPV6.sub(TOKEN, out)
    out = _REPEATED_TOKEN.sub(TOKEN + " ", out)
    out = " ".join(out.split()).strip(" ,;:")
    if len(out) > MAX_CHARS:
        out = out[: MAX_CHARS - 3].rstrip() + "..."
    return out


def redact_source_text(text: str) -> str:
    """Redact an already-rendered source-error string. Never raises.

    Returns a one-line, bounded, client-safe rendering — a classified constant
    where we recognise the failure, otherwise the scrubbed original. Falls back
    to a generic line only when nothing readable survives.
    """
    raw = _strip_appendix(" ".join((text or "").split()))
    # Bound the input BEFORE any scanning. The scrub patterns are quadratic in
    # the worst case, and a driver message is not a fixed-size input: engines
    # echo the caller's identifiers back (DuckDB's "Referenced column ... not
    # found" repeats the whole name), so an analyst-authored query can inflate
    # it at will and burn CPU on a threadpool worker. Truncating first is safe:
    # a bisected DSN or host=/port= pair on the surviving prefix still matches,
    # and classification phrases appear early in driver messages. The tail
    # truncation in _scrub still enforces the output bound afterwards, since
    # "[redacted]" substitutions can lengthen the string.
    raw = raw[: 4 * MAX_CHARS]
    if not raw:
        return _UNRECOGNIZED
    for pattern, reason in _REASONS:
        if pattern.search(raw):
            return reason
    return _scrub(raw) or _UNRECOGNIZED


def redact_source_error(exc: BaseException, *, action: str) -> str:
    """Log the full exception, return the client-safe rendering.

    ``action`` describes what failed, in a form that carries the ids an operator
    needs to find the row ("read the schema for dataset 12"). It goes to the log
    line only — the returned string is what a client may see.
    """
    log.warning(
        "Source error while trying to %s",
        action,
        exc_info=exc,
        extra={"event": "source_error"},
    )
    safe = redact_source_text(str(exc))
    if safe == _UNRECOGNIZED:
        # Nothing readable survived (or the exception carried no message at all):
        # the class name is the most specific thing we can say that is certainly
        # safe — it is our own code's vocabulary, not the source's.
        return f"the source driver reported {type(exc).__name__} (see the server log for this request id)"
    return safe


def redact_probe_message(message: str, *, action: str) -> str:
    """Redact a source error the connector already caught and rendered as text.

    ``Connector.test()`` returns ``(False, "Connection failed: ...")`` rather than
    raising, so there is no exception to log — log the raw text here instead, so
    the operator-facing detail still lands under the request id.
    """
    log.warning(
        "Source error while trying to %s: %s",
        action,
        message,
        extra={"event": "source_error"},
    )
    return redact_source_text(_PROBE_PREFIX.sub("", message or ""))
