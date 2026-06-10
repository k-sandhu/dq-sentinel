"""Test setup: point the app DB at a temp file BEFORE app modules build engines,
and provide a small synthetic source database with known issues.
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="dqsentinel-test-"))
os.environ["DQ_DATABASE_URL"] = f"sqlite:///{(_TMP / 'app.db').as_posix()}"
os.environ["DQ_SECRET_KEY"] = "test-secret-0123456789abcdef0123456789abcdef"
os.environ["DQ_BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
os.environ["DQ_BOOTSTRAP_ADMIN_PASSWORD"] = "admin123"
os.environ["ANTHROPIC_API_KEY"] = ""  # LLM features must degrade in tests

from app.db import init_db, reset_for_tests  # noqa: E402

reset_for_tests()

# Known truths about the source fixture (asserted in tests)
SOURCE_ROWS = 200
NULL_EMAILS = 5
BAD_EMAILS = 2  # "not-an-email", "a@b"
DUP_EMAIL_GROUPS = 1  # one email shared by 2 rows
BAD_STATUS = 1  # one "x"
HUGE_AGE = 1  # one 999


@pytest.fixture(scope="session")
def source_db() -> str:
    """Create the synthetic source sqlite DB. Returns its DSN."""
    path = _TMP / "source.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE people (id INTEGER PRIMARY KEY, email TEXT, age INTEGER,"
        " status TEXT, score REAL, created_at TEXT)"
    )
    now = datetime.now()
    rows = []
    for i in range(1, SOURCE_ROWS + 1):
        email: str | None = f"user{i}@example.com"
        if i <= NULL_EMAILS:
            email = None
        elif i == 6:
            email = "not-an-email"
        elif i == 7:
            email = "a@b"
        elif i == 8:
            email = "user9@example.com"  # duplicate of row 9
        age = 18 + (i % 48)
        if i == 10:
            age = 999
        status = "active" if i % 3 else "inactive"
        if i == 11:
            status = "x"
        score = 50.0 + (i % 10)  # tight cluster for outlier tests
        created = (now - timedelta(hours=2, minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
        if i == 12:  # future-dated row: must not mask staleness in freshness checks
            created = (now + timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        rows.append((i, email, age, status, score, created))
    con.executemany("INSERT INTO people VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_headers(client) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
