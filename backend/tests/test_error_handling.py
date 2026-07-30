"""Error-handling contract (#282).

Three related guarantees:
1. Any unhandled exception becomes a consistent JSON 500 carrying the request id
   (body + ``X-Request-ID``), with no exception text leaked to the client.
2. A validly-signed token with a missing/non-numeric ``sub`` is a **401**, not a 500.
3. Contract conformance degrades honestly (schema clause -> ``unknown`` + reason)
   when the source database cannot be reached, instead of raw-500ing.

``HTTPException`` and request-validation (422) handling must be untouched by all
of the above.
"""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import jwt
import pytest

from app.config import get_settings

# Not pytest's ``tmp_path``: this suite's conftest already owns a private temp dir
# and pytest's basetemp is not always writable on the Windows dev boxes.
_TMP = Path(tempfile.mkdtemp(prefix="dqsentinel-errors-"))


def _signed_token(**claims: Any) -> str:
    """A token signed with the real app secret — only the claims are odd."""
    payload: dict[str, Any] = {
        "email": "admin@example.com",
        "role": "admin",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    payload.update(claims)
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- (2) 401, not 500


@pytest.mark.parametrize(
    ("label", "claims"),
    [
        ("missing sub", {}),
        ("non-numeric sub", {"sub": "not-a-number"}),
        ("null sub", {"sub": None}),
        ("object sub", {"sub": {"id": 1}}),
    ],
)
def test_signed_token_with_bad_sub_is_401_not_500(client, label, claims):
    resp = client.get("/api/v1/auth/me", headers=_bearer(_signed_token(**claims)))
    assert resp.status_code == 401, f"{label}: {resp.status_code} {resp.text}"
    assert resp.json()["detail"] == "Invalid or expired token"


def test_valid_token_still_authenticates(client, admin_headers):
    resp = client.get("/api/v1/auth/me", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "admin@example.com"

    # A numeric-string sub for a real user keeps working (the normal issued shape).
    user_id = resp.json()["id"]
    hand_rolled = client.get("/api/v1/auth/me", headers=_bearer(_signed_token(sub=str(user_id))))
    assert hand_rolled.status_code == 200, hand_rolled.text
    assert hand_rolled.json()["id"] == user_id


def test_unknown_but_numeric_sub_is_401(client):
    resp = client.get("/api/v1/auth/me", headers=_bearer(_signed_token(sub="99999999")))
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "User not found or inactive"


# ------------------------------------------------------- (1) global exception handler


def test_unhandled_exception_returns_detail_and_request_id(client):
    from app.main import app

    path = f"/__test__/boom-{uuid4().hex[:8]}"
    secret = "kaboom-internal-detail-do-not-leak"

    def boom() -> dict[str, str]:
        raise RuntimeError(secret)

    app.add_api_route(path, boom, methods=["GET"], include_in_schema=False)
    try:
        resp = client.get(path, headers={"X-Request-ID": "rid-boom-test"})
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != path]

    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert set(body) == {"detail", "request_id"}, body
    assert isinstance(body["detail"], str) and body["detail"]
    assert secret not in resp.text  # exception text stays server-side
    assert "RuntimeError" not in resp.text
    # Correlation: the caller-supplied id comes back in the body AND the header.
    assert body["request_id"] == "rid-boom-test"
    assert resp.headers["X-Request-ID"] == "rid-boom-test"


def test_unhandled_exception_generates_a_request_id_when_caller_sends_none(client):
    from app.main import app

    path = f"/__test__/boom-{uuid4().hex[:8]}"

    def boom() -> dict[str, str]:
        raise ValueError("nope")

    app.add_api_route(path, boom, methods=["GET"], include_in_schema=False)
    try:
        resp = client.get(path)
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != path]

    assert resp.status_code == 500, resp.text
    rid = resp.json()["request_id"]
    assert rid and rid != "-"
    assert resp.headers["X-Request-ID"] == rid


def test_http_exception_and_validation_errors_are_unaffected(client, admin_headers):
    missing = client.get("/api/v1/datasets/99999999", headers=admin_headers)
    assert missing.status_code == 404, missing.text
    assert missing.json()["detail"] == "Not found"

    unauth = client.get("/api/v1/connections")
    assert unauth.status_code == 401, unauth.text
    assert unauth.json()["detail"] == "Not authenticated"

    forbidden_shape = client.post("/api/v1/auth/login", json={"email": "nobody@example.com"})
    assert forbidden_shape.status_code == 422, forbidden_shape.text
    assert isinstance(forbidden_shape.json()["detail"], list)  # FastAPI's error list

    ok = client.get("/api/v1/health")
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ok"


# ------------------------------------------- (3) conformance degrades on a dead source


@pytest.fixture
def unreachable_dataset(client, admin_headers) -> dict[str, Any]:
    """A dataset whose connection DSN points at a sqlite file that does not exist."""
    dsn = f"sqlite:///{(_TMP / f'no-such-database-{uuid4().hex[:8]}.sqlite').as_posix()}"
    conn = client.post(
        "/api/v1/connections",
        # Connection.name is unique and the app DB is shared session-wide -> uuid it.
        json={"name": f"unreachable-src-{uuid4().hex[:12]}", "dsn": dsn},
        headers=admin_headers,
    )
    assert conn.status_code == 201, conn.text
    resp = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn.json()["id"], "tables": [{"table_name": "people"}]},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]


def test_conformance_reports_unknown_when_source_is_unreachable(
    client, admin_headers, unreachable_dataset
):
    created = client.post(
        f"/api/v1/datasets/{unreachable_dataset['id']}/contract",
        json={
            "name": "Contract on a dead source",
            "version": "1.0.0",
            "spec": {
                "schema": {
                    "columns": [{"name": "id", "dtype": "INTEGER", "required": True}],
                    "allow_extra_columns": True,
                },
                "quality": [
                    {
                        "id": "email-not-null",
                        "name": "Email populated",
                        "check_type": "not_null",
                        "column": "email",
                    }
                ],
            },
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    contract_id = created.json()["id"]

    conf = client.get(
        f"/api/v1/datasets/{unreachable_dataset['id']}/contract/{contract_id}/conformance",
        headers=admin_headers,
    )
    assert conf.status_code == 200, conf.text
    body = conf.json()
    schema_clause = next(c for c in body["clauses"] if c["kind"] == "schema")
    assert schema_clause["status"] == "unknown"
    assert "Could not read the source schema" in schema_clause["detail"]
    assert schema_clause["observed"] == {}
    # Expectations are still reported, so the UI can show what was promised.
    assert schema_clause["expected"]["columns"][0]["name"] == "id"
    # The non-schema clauses keep computing normally (no run yet -> unknown).
    quality_clause = next(c for c in body["clauses"] if c["kind"] == "quality")
    assert quality_clause["status"] == "unknown"
    assert body["status"] == "unknown"

    # The /contract/conformance (latest) alias degrades the same way.
    latest = client.get(
        f"/api/v1/datasets/{unreachable_dataset['id']}/contract/conformance",
        headers=admin_headers,
    )
    assert latest.status_code == 200, latest.text
    assert next(c for c in latest.json()["clauses"] if c["kind"] == "schema")["status"] == "unknown"


def test_starter_contract_on_unreachable_source_is_502_not_500(
    client, admin_headers, unreachable_dataset
):
    """No spec + no profile means the source is the only column oracle. It must
    fail cleanly rather than raw-500 (and must not write an empty contract)."""
    resp = client.post(
        f"/api/v1/datasets/{unreachable_dataset['id']}/contract",
        json={"name": "Starter", "version": "0.1.0"},
        headers=admin_headers,
    )
    assert resp.status_code == 502, resp.text
    assert "Could not read the source schema" in resp.json()["detail"]

    listed = client.get(
        f"/api/v1/datasets/{unreachable_dataset['id']}/contracts", headers=admin_headers
    )
    assert listed.status_code == 200, listed.text
    assert listed.json() == []
