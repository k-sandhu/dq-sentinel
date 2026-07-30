"""Source-driver errors must not echo the source's host/port/user (#307).

Driver text routinely carries infrastructure — ``connection to server at
"db.internal" (10.0.0.5), port 5432 failed: FATAL: password authentication
failed for user "svc_dq"``. Every path that renders a source failure to a client
goes through the one redactor in ``app/core/errors.py``.

The bar is two-sided, and both halves are asserted here:
  * the response body carries NO host, port, account or DSN, and no SQLAlchemy
    ``[SQL: ...]`` tail (which on ``/query/run`` is the rewritten guard SQL);
  * the response is still ACTIONABLE — a recognised failure names its cause, an
    unrecognised one keeps its wording with only the infrastructure removed;
  * the FULL exception reaches the structured server log.
"""

import logging
import time
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.core.errors import redact_probe_message, redact_source_error, redact_source_text

# The exact leak shape called out in #307.
SECRETS = ("db.internal", "5432", "svc_dq")
LIBPQ_TEXT = "connection attempt failed (host=db.internal port=5432 user=svc_dq)"
PG_AUTH_TEXT = (
    'connection to server at "db.internal" (10.0.0.5), port 5432 failed: '
    'FATAL: password authentication failed for user "svc_dq"'
)


@contextmanager
def captured_source_log():
    """Collect what the redactor logs.

    A private handler on the redactor's own logger rather than ``caplog``: the app
    calls ``configure_logging()``, which replaces the ROOT handlers, so a
    root-attached capture can be evicted depending on fixture ordering.
    """
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("app.core.errors")
    handler = _Collect(level=logging.DEBUG)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def rendered(records: list[logging.LogRecord]) -> str:
    """Records as the log file would show them (message + formatted traceback)."""
    formatter = logging.Formatter("%(message)s")
    return "\n".join(formatter.format(r) for r in records)


def sqlalchemy_error(message: str, sql: str = "SELECT secret FROM people LIMIT 100"):
    """A driver error wrapped the way SQLAlchemy hands it to us: the DBAPI class
    in front, the executed statement and its parameters appended."""
    return OperationalError(sql, {"p": "value"}, Exception(message))


class _FailingConnector:
    """Stands in for a connector whose source is unreachable."""

    kind = "postgresql"

    def __init__(self, message: str = LIBPQ_TEXT):
        self._message = message

    def _boom(self, *_args: Any, **_kwargs: Any):
        raise sqlalchemy_error(self._message)

    list_tables = _boom
    get_columns = _boom
    schema_tree = _boom
    run_select = _boom

    def test(self) -> tuple[bool, str, int | None]:
        # Connector.test() swallows the driver error and renders it as text.
        return False, f"Connection failed: {self._message}", None


# ------------------------------------------------------------------ the redactor


def test_redactor_removes_host_port_and_user_but_keeps_the_wording():
    safe = redact_source_text(LIBPQ_TEXT)
    for secret in SECRETS:
        assert secret not in safe, safe
    # Still actionable: the failure's own words survive, and the KEY of each
    # redacted value stays so an operator knows which knob was involved.
    assert "connection attempt failed" in safe
    assert "host=" in safe and "port=" in safe and "user=" in safe


@pytest.mark.parametrize(
    ("driver_text", "expected"),
    [
        (PG_AUTH_TEXT, "authentication with the source failed"),
        ("Access denied for user 'svc_dq'@'db.internal' (using password: YES)",
         "authentication with the source failed"),
        ('could not translate host name "db.internal" to address: Unknown host',
         "the source host could not be resolved"),
        ("could not connect to server: Connection refused. Is the server running on db.internal?",
         "the source refused the connection"),
        ("TCP Provider: Timeout expired while connecting to db.internal:5432",
         "the source did not respond in time"),
        ("unable to open database file", "the source database file could not be opened"),
        ('permission denied for table orders', "the source account is not permitted to read this object"),
    ],
)
def test_recognised_failures_become_a_fixed_safe_sentence(driver_text, expected):
    """Classification is the primary control: the answer is a constant from the
    redactor, so nothing from the driver text can ride along."""
    assert redact_source_text(driver_text) == expected


def test_sqlalchemy_statement_and_parameters_are_dropped():
    safe = redact_source_text(str(sqlalchemy_error("no such column: emial")))
    assert "no such column: emial" in safe, "the analyst's own SQL error must survive"
    assert "[SQL:" not in safe
    assert "[parameters:" not in safe
    assert "SELECT secret" not in safe
    assert "sqlalche.me" not in safe


def test_dsn_and_ip_literals_are_removed():
    safe = redact_source_text("could not open postgresql://svc_dq:hunter2@db.internal:5432/warehouse")
    for secret in (*SECRETS, "hunter2", "postgresql://"):
        assert secret not in safe, safe


def test_qualified_table_names_survive_redaction():
    """The scrub must not eat ``schema.table`` — over-redaction is how a safety
    net turns into an unusable workbench."""
    assert "public.orders" in redact_source_text('relation "public.orders" does not exist')


def test_redact_source_error_logs_the_full_exception():
    with captured_source_log() as records:
        safe = redact_source_error(sqlalchemy_error(PG_AUTH_TEXT), action="probe connection 7")
    log_text = rendered(records)
    for secret in SECRETS:
        assert secret not in safe
        assert secret in log_text, f"{secret} must survive in the operator log"
    assert "probe connection 7" in log_text


def test_empty_driver_message_falls_back_to_the_exception_class():
    with captured_source_log():
        safe = redact_source_error(RuntimeError(""), action="do a thing")
    assert "RuntimeError" in safe


def test_probe_message_drops_the_connectors_own_lead_in():
    with captured_source_log() as records:
        safe = redact_probe_message(f"Connection failed: {PG_AUTH_TEXT}", action="probe")
    assert safe == "authentication with the source failed"
    assert "db.internal" in rendered(records)


# ------------------------------------------------------------------- API surfaces


@pytest.fixture
def connection_id(client, admin_headers, source_db) -> int:
    resp = client.post(
        "/api/v1/connections",
        json={"name": f"redaction-src-{uuid4().hex[:12]}", "dsn": source_db},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_list_tables_502_carries_the_reason_not_the_source_internals(
    client, admin_headers, connection_id, monkeypatch
):
    monkeypatch.setattr("app.api.connections.connector_for", lambda _c: _FailingConnector())
    with captured_source_log() as records:
        resp = client.get(f"/api/v1/connections/{connection_id}/tables", headers=admin_headers)
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith("Could not introspect the source:")
    for secret in SECRETS:
        assert secret not in resp.text, detail
        assert secret in rendered(records)
    assert "[SQL:" not in resp.text


def test_connection_schema_502_is_redacted(client, admin_headers, connection_id, monkeypatch):
    monkeypatch.setattr("app.api.query.connector_for", lambda _c: _FailingConnector())
    with captured_source_log() as records:
        resp = client.get(f"/api/v1/connections/{connection_id}/schema", headers=admin_headers)
    assert resp.status_code == 502, resp.text
    for secret in SECRETS:
        assert secret not in resp.text
        assert secret in rendered(records)


def test_fleet_health_message_is_redacted(client, admin_headers, connection_id, monkeypatch):
    monkeypatch.setattr(
        "app.api.connections.connector_for", lambda _c: _FailingConnector(PG_AUTH_TEXT)
    )
    with captured_source_log() as records:
        resp = client.get("/api/v1/connections/health", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json() if r["id"] == connection_id)
    assert row["ok"] is False
    # Actionable without being a disclosure.
    assert row["message"] == "Could not connect to this source: authentication with the source failed"
    for secret in SECRETS:
        assert secret not in resp.text
        assert secret in rendered(records)


def test_saved_connection_test_is_redacted(client, admin_headers, connection_id, monkeypatch):
    """A saved connection can be visible to someone who never saw its DSN."""
    monkeypatch.setattr(
        "app.api.connections.connector_for", lambda _c: _FailingConnector(PG_AUTH_TEXT)
    )
    with captured_source_log():
        resp = client.post(f"/api/v1/connections/{connection_id}/test", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["message"] == "Could not connect to this source: authentication with the source failed"
    for secret in SECRETS:
        assert secret not in resp.text


def test_query_run_400_keeps_the_sql_error_and_drops_the_guard_sql(
    client, admin_headers, connection_id
):
    """A real broken query against a real source: the analyst still gets the
    driver's diagnosis, without the statement the guard rewrote."""
    resp = client.post(
        "/api/v1/query/run",
        json={"connection_id": connection_id, "sql": "SELECT emial FROM people"},
        headers=admin_headers,
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "no such column" in detail and "emial" in detail
    assert "[SQL:" not in detail
    assert "LIMIT" not in detail, detail
    assert "sqlalche.me" not in detail


def test_query_run_400_is_redacted_when_the_source_is_the_problem(
    client, admin_headers, connection_id, monkeypatch
):
    monkeypatch.setattr("app.api.query.connector_for", lambda _c: _FailingConnector())
    with captured_source_log() as records:
        resp = client.post(
            "/api/v1/query/run",
            json={"connection_id": connection_id, "sql": "SELECT 1"},
            headers=admin_headers,
        )
    assert resp.status_code == 400, resp.text
    for secret in SECRETS:
        assert secret not in resp.text
        assert secret in rendered(records)


def test_contract_conformance_detail_is_redacted(
    client, admin_headers, connection_id, monkeypatch
):
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": connection_id, "tables": [{"table_name": "people"}]},
        headers=admin_headers,
    ).json()[0]
    created = client.post(
        f"/api/v1/datasets/{ds['id']}/contract",
        json={
            "name": "Redaction contract",
            "version": "1.0.0",
            "spec": {"schema": {"columns": [{"name": "id", "dtype": "INTEGER", "required": True}]}},
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text

    monkeypatch.setattr(
        "app.core.contracts.connector_for", lambda _c: _FailingConnector(PG_AUTH_TEXT)
    )
    with captured_source_log() as records:
        conf = client.get(
            f"/api/v1/datasets/{ds['id']}/contract/{created.json()['id']}/conformance",
            headers=admin_headers,
        )
    assert conf.status_code == 200, conf.text
    schema_clause = next(c for c in conf.json()["clauses"] if c["kind"] == "schema")
    assert schema_clause["status"] == "unknown"
    assert schema_clause["detail"] == (
        "Could not read the source schema: authentication with the source failed"
    )
    for secret in SECRETS:
        assert secret not in conf.text
        assert secret in rendered(records)


def test_starter_contract_502_is_redacted(client, admin_headers, connection_id, monkeypatch):
    """No profile and no spec: the source is the only column oracle, so the 502
    body is the first thing the analyst sees — and the most tempting place to
    paste a raw driver message."""
    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": connection_id, "tables": [{"table_name": "people"}]},
        headers=admin_headers,
    ).json()[0]
    monkeypatch.setattr(
        "app.core.contracts.connector_for", lambda _c: _FailingConnector(PG_AUTH_TEXT)
    )
    with captured_source_log() as records:
        resp = client.post(
            f"/api/v1/datasets/{ds['id']}/contract",
            json={"name": "Starter", "version": "0.1.0"},
            headers=admin_headers,
        )
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith("Could not read the source schema to draft a contract:")
    assert "authentication with the source failed" in detail
    for secret in SECRETS:
        assert secret not in resp.text
        assert secret in rendered(records)


# ---------------------------------------------------------------------------
# Review-round regressions (#307 follow-up). Each of these passed the original
# implementation's own test suite, which is why they are pinned explicitly.
# ---------------------------------------------------------------------------


def test_redaction_is_bounded_on_an_attacker_inflated_message() -> None:
    """A driver message is not a fixed-size input: engines echo the caller's
    identifiers back, so an analyst-authored query can inflate it at will. The
    scrub patterns are quadratic in the worst case, so the input must be bounded
    BEFORE scanning -- otherwise a cheap request burns ~45s on a worker thread."""
    ident = ".".join(["a"] * 4000)
    msg = f'Binder Error: Referenced column "{ident}" not found in FROM clause!'
    start = time.perf_counter()
    out = redact_source_text(msg)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"redaction took {elapsed:.1f}s on a {len(msg)}-char message"
    assert len(out) <= 300


def test_mssql_odbc_tag_does_not_eat_the_diagnosis() -> None:
    r"""pyodbc injects a literal "[SQL Server]" tag into every MSSQL error. An
    appendix pattern anchored on `\[SQL\b` swallowed it and everything after,
    deleting the diagnosis -- and deleting "Login failed for user" before the
    classifier could see it, so the primary control never fired on mssql."""
    login = (
        "('28000', \"[28000] [Microsoft][ODBC Driver 18 for SQL Server]"
        "[SQL Server]Login failed for user 'svc'. (18456)\")"
    )
    assert redact_source_text(login) == "authentication with the source failed"

    bad_column = (
        "('42S22', \"[42S22] [Microsoft][ODBC Driver 18 for SQL Server]"
        "[SQL Server]Invalid column name 'emial'.\")"
    )
    assert "Invalid column name 'emial'" in redact_source_text(bad_column)


def test_sqlalchemy_appendix_is_still_stripped() -> None:
    """Guard the above fix from over-correcting: SQLAlchemy's own
    [SQL: ...] / [parameters: ...] / [cached since ...] tails must still go."""
    text = (
        "(sqlite3.OperationalError) no such column: emial "
        "[SQL: SELECT emial FROM people] [parameters: {'p': 1}]"
    )
    assert redact_source_text(text) == "no such column: emial"
    assert redact_source_text("boom [cached since 12s ago]") == "boom"


def test_snowflake_missing_object_is_not_reported_as_a_grant_failure() -> None:
    """Snowflake deliberately says "does not exist or not authorized" for a plain
    typo so it does not disclose existence. Classifying that as a permission
    error sends the analyst hunting a grant they already have."""
    typo = (
        "002003 (42S02): SQL compilation error: "
        "Object 'ANALYTICS.PUBLIC.ORDRES' does not exist or not authorized."
    )
    assert "not permitted" not in redact_source_text(typo)
    assert "ORDRES" in redact_source_text(typo)

    grant = (
        "003001 (42501): SQL access control error: "
        "Insufficient privileges to operate on table 'ORDERS'"
    )
    assert redact_source_text(grant) == "the source account is not permitted to read this object"
