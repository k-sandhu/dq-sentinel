"""Exception "why it failed" attribution (D6 / #176).

Proves the endpoint is viewer-readable + connection-scoped (404 for ungranted), the
deterministic good-vs-bad + column attribution computes, PII is redacted in both row
sets and absent from factors, samples/factors are capped, and non-computable checks
return an honest reason. Plus a pure-function unit test for the scorer.

App DB is session-shared → globally-unique connection names; the source table is the
conftest `people` fixture (status active/inactive + one "x"; emails null for rows 1-5).
"""

from app.core.attribution import attribution_factors
from app.db import session_factory
from app.models import Check, CheckRun, Connection, Dataset, ExceptionRecord, TableKnowledge

_Session = session_factory()


def _login(client, email, password):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _uid(client, headers, email):
    return next(u["id"] for u in client.get("/api/v1/auth/users", headers=headers).json() if u["email"] == email)


def _setup(db, source_db, *, conn_name, col, check_type, params, fail_rows, pii=None):
    conn = Connection(name=conn_name, kind="sqlite", dsn=source_db)
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, schema_name=None, table_name="people", display_name="people")
    db.add(ds)
    db.flush()
    if pii is not None:
        db.add(TableKnowledge(dataset_id=ds.id, pii_columns=pii))
    chk = Check(
        dataset_id=ds.id, name=f"{conn_name}-chk", check_type=check_type,
        column_name=col, params=params, status="active", last_status="fail",
    )
    db.add(chk)
    db.flush()
    run = CheckRun(check_id=chk.id, dataset_id=ds.id, status="fail")
    db.add(run)
    db.flush()
    excs = []
    for rd in fail_rows:
        e = ExceptionRecord(run_id=run.id, check_id=chk.id, dataset_id=ds.id, row_data=rd, status="open")
        db.add(e)
        db.flush()
        excs.append(e.id)
    return conn.id, excs


def test_attribution_happy_path(client, admin_headers, source_db):
    with _Session() as db:
        _, excs = _setup(
            db, source_db, conn_name="d6-happy", col="status", check_type="accepted_values",
            params={"values": ["active", "inactive"]},
            fail_rows=[{"id": 11, "email": "u11@example.com", "status": "x"}],
        )
        db.commit()

    r = client.get(f"/api/v1/exceptions/{excs[0]}/attribution", headers=admin_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["computable"] is True and data["reason"] == ""
    assert data["bad_rows"] and data["good_rows"]  # both samples present
    top = data["factors"][0]
    assert top["column"] == "status" and "x" in top["label"] and top["pct"] == 100
    assert "status = " in data["summary"]


def test_attribution_404_for_ungranted_and_missing(client, admin_headers, source_db):
    with _Session() as db:
        _, excs = _setup(
            db, source_db, conn_name="d6-scope", col="status", check_type="accepted_values",
            params={"values": ["active", "inactive"]}, fail_rows=[{"id": 11, "status": "x"}],
        )
        db.commit()

    # a user with no grant on this exception's connection -> 404 (don't leak existence)
    client.post("/api/v1/auth/users",
                json={"email": "d6-nogrant@x.com", "name": "N", "password": "password1", "role": "editor"},
                headers=admin_headers)
    uid = _uid(client, admin_headers, "d6-nogrant@x.com")
    # grant them a DIFFERENT connection so they are a restricted (non-legacy) user
    other = client.post("/api/v1/connections", json={"name": "d6-other-src", "dsn": source_db}, headers=admin_headers).json()
    client.post(f"/api/v1/auth/users/{uid}/grants",
                json={"connection_id": other["id"], "role": "viewer"}, headers=admin_headers)
    h = _login(client, "d6-nogrant@x.com", "password1")
    assert client.get(f"/api/v1/exceptions/{excs[0]}/attribution", headers=h).status_code == 404
    assert client.get("/api/v1/exceptions/99999999/attribution", headers=admin_headers).status_code == 404


def test_attribution_redacts_pii_in_rows_and_factors(client, admin_headers, source_db):
    with _Session() as db:
        _, excs = _setup(
            db, source_db, conn_name="d6-pii", col="status", check_type="accepted_values",
            params={"values": ["active", "inactive"]}, pii=["email"],
            fail_rows=[{"id": 11, "email": "secret@evil.com", "status": "x"}],
        )
        db.commit()

    data = client.get(f"/api/v1/exceptions/{excs[0]}/attribution", headers=admin_headers).json()
    raw = client.get(f"/api/v1/exceptions/{excs[0]}/attribution", headers=admin_headers).text
    assert "secret@evil.com" not in raw  # the planted pii value never ships
    for row in data["good_rows"] + data["bad_rows"]:
        ei = row["columns"].index("email")
        assert row["cells"][ei] in (None, "[REDACTED]")  # never a real email
    assert all(f["column"] != "email" for f in data["factors"])  # pii column not an attribution factor


def test_attribution_caps(client, admin_headers, source_db):
    from app.core.attribution import MAX_FACTORS, SAMPLE_ROWS

    rows = [{"id": i, "email": f"u{i}@x.com", "status": "x"} for i in range(20)]
    with _Session() as db:
        _, excs = _setup(
            db, source_db, conn_name="d6-caps", col="status", check_type="accepted_values",
            params={"values": ["active", "inactive"]}, fail_rows=rows,
        )
        db.commit()
    data = client.get(f"/api/v1/exceptions/{excs[0]}/attribution", headers=admin_headers).json()
    assert len(data["bad_rows"]) <= SAMPLE_ROWS
    assert len(data["good_rows"]) <= SAMPLE_ROWS
    assert len(data["factors"]) <= MAX_FACTORS


def test_attribution_not_computable_without_predicate(client, admin_headers, source_db):
    with _Session() as db:
        _, excs = _setup(
            db, source_db, conn_name="d6-uncomp", col="email", check_type="unique",
            params={}, fail_rows=[{"id": 8, "email": "user9@example.com"}],
        )
        db.commit()
    data = client.get(f"/api/v1/exceptions/{excs[0]}/attribution", headers=admin_headers).json()
    assert data["computable"] is False
    assert data["reason"] == "no_row_predicate"
    assert data["factors"] == []


def test_attribution_factors_pure():
    bad = [{"status": "refunded", "amt": -5}, {"status": "refunded", "amt": -9}]
    good = [{"status": "paid", "amt": 10}, {"status": "paid", "amt": 20}]
    factors = attribution_factors(bad, good, ["status", "amt"], pii_columns=[])
    assert factors[0]["column"] == "status"
    assert factors[0]["pct"] == 100  # 100% of failing rows are 'refunded'...
    assert factors[0]["healthy_count"] == 0  # ...and none of the healthy ones
    # a pii column is dropped entirely
    assert all(f["column"] != "status" for f in attribution_factors(bad, good, ["status"], pii_columns=["status"]))
