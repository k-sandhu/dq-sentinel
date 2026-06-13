import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pytest

from app.connectors.sa import Connector
from app.core.check_types import CheckContext, run_check_type, validate_check
from tests.conftest import BAD_EMAILS, BAD_STATUS, HUGE_AGE, NULL_EMAILS, SOURCE_ROWS


@pytest.fixture(scope="module")
def ctx_factory(source_db):
    connector = Connector(source_db)

    def make(column=None, params=None):
        return CheckContext(
            connector=connector, table="people", schema=None, column=column, params=params or {}
        )

    return make


def test_not_null(ctx_factory):
    r = run_check_type(ctx_factory("email"), "not_null")
    assert r.violation_count == NULL_EMAILS
    assert len(r.sample_rows) == NULL_EMAILS
    assert all(row["email"] is None for row in r.sample_rows)


def test_unique(ctx_factory):
    r = run_check_type(ctx_factory("email"), "unique")
    assert r.metrics["duplicate_groups"] == 1
    assert r.metrics["duplicated_rows"] == 2
    assert r.violation_count == 1  # one surplus duplicate row
    assert len(r.sample_rows) == 2


def test_accepted_values(ctx_factory):
    r = run_check_type(
        ctx_factory("status", {"values": ["active", "inactive"]}), "accepted_values"
    )
    assert r.violation_count == BAD_STATUS
    assert r.sample_rows[0]["status"] == "x"


def test_range(ctx_factory):
    r = run_check_type(ctx_factory("age", {"min": 0, "max": 120}), "range")
    assert r.violation_count == HUGE_AGE
    assert r.sample_rows[0]["age"] == 999


def test_string_length(ctx_factory):
    r = run_check_type(ctx_factory("email", {"min_len": 6}), "string_length")
    assert r.violation_count == 1  # "a@b"


def test_regex_python_fallback(ctx_factory):
    r = run_check_type(
        ctx_factory("email", {"pattern": r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$"}), "regex_match"
    )
    assert r.violation_count == BAD_EMAILS
    assert r.metrics["engine"] == "python-fallback"


def test_freshness(ctx_factory):
    stale = run_check_type(ctx_factory("created_at", {"max_age_hours": 1}), "freshness")
    assert stale.violation_count == 1
    fresh = run_check_type(ctx_factory("created_at", {"max_age_hours": 100}), "freshness")
    assert fresh.violation_count == 0
    assert fresh.metrics["age_hours"] > 1
    # the future-dated row is excluded from staleness and surfaced as a metric
    assert fresh.metrics["future_rows"] == 1
    assert fresh.metrics["age_hours"] < 200  # not computed from the +10d row


def test_row_count_min(ctx_factory):
    low = run_check_type(ctx_factory(None, {"min_rows": SOURCE_ROWS + 1}), "row_count_min")
    assert low.violation_count == 1
    ok = run_check_type(ctx_factory(None, {"min_rows": 10}), "row_count_min")
    assert ok.violation_count == 0


def test_row_count_anomaly_needs_history(ctx_factory):
    r = run_check_type(ctx_factory(None, {}), "row_count_anomaly")
    assert r.violation_count == 0
    assert r.metrics["row_count"] == SOURCE_ROWS


def test_custom_sql(ctx_factory):
    r = run_check_type(
        ctx_factory(None, {"sql": "SELECT * FROM people WHERE age > 500"}), "custom_sql"
    )
    assert r.violation_count == HUGE_AGE


def test_custom_sql_rejects_writes(ctx_factory):
    from app.connectors.safety import SqlNotAllowed

    with pytest.raises(SqlNotAllowed):
        run_check_type(ctx_factory(None, {"sql": "DELETE FROM people"}), "custom_sql")


def test_ml_outlier_finds_planted_row(ctx_factory):
    r = run_check_type(ctx_factory(None, {"contamination": 0.01}), "ml_outlier")
    assert r.rows_evaluated == SOURCE_ROWS
    flagged_ages = {row["age"] for row in r.sample_rows}
    assert 999 in flagged_ages  # the planted outlier must be caught
    assert r.scores and r.scores[0] >= max(r.scores)  # sorted by score desc


def test_validate_check():
    assert validate_check("not_null", "email", {}) == {}
    with pytest.raises(ValueError):
        validate_check("not_null", None, {})
    with pytest.raises(ValueError):
        validate_check("nope", "x", {})
    with pytest.raises(ValueError):
        validate_check("regex_match", "email", {"pattern": "("})  # invalid regex
    params = validate_check("accepted_values", "status", {"values": ["a"], "junk": 1})
    assert "junk" not in params


def test_validate_drift_method():
    assert validate_check("distribution_drift", "x", {"method": "KS"})["method"] == "ks"
    assert validate_check("distribution_drift", "x", {})["method"] == "psi"  # default
    with pytest.raises(ValueError):
        validate_check("distribution_drift", "x", {"method": "wasserstein"})


# --------------------------------------------------------------- distribution_drift
# These tests need a baseline Profile (PSI) and run history (KS), so they build a
# real source table, profile it into the app DB, and run via a db-aware context.

from app.core.profiler import profile_dataset  # noqa: E402
from app.db import init_db, session_factory  # noqa: E402
from app.models import Check, CheckRun, Connection, Dataset, Profile  # noqa: E402


def _make_source(tmp_dir: Path, name: str, columns: dict[str, list]) -> str:
    """Write a one-table sqlite DB ('t') with the given columns; return its DSN."""
    path = tmp_dir / f"{name}.sqlite"
    con = sqlite3.connect(path)
    coldefs = ", ".join(
        f"{c} {'REAL' if isinstance(v[0], float) else 'TEXT' if isinstance(v[0], str) else 'INTEGER'}"
        for c, v in columns.items()
    )
    con.execute(f"CREATE TABLE t ({coldefs})")
    names = list(columns)
    n = len(next(iter(columns.values())))
    placeholders = ", ".join(["?"] * len(names))
    con.executemany(
        f"INSERT INTO t ({', '.join(names)}) VALUES ({placeholders})",
        [tuple(columns[c][i] for c in names) for i in range(n)],
    )
    con.commit()
    con.close()
    return f"sqlite:///{path.as_posix()}"


def _drift_ctx(db, dsn: str, column: str, params: dict, baseline_dsn: str | None = None):
    """Persist a Connection/Dataset, profile `baseline_dsn` (default = dsn) into a
    Profile row, create a Check, and return a db-aware CheckContext over `dsn`."""
    conn = Connection(name=f"c-{column}-{id(params)}", kind="sqlite", dsn=dsn)
    db.add(conn)
    db.flush()
    ds = Dataset(connection_id=conn.id, schema_name=None, table_name="t")
    db.add(ds)
    db.flush()

    prof = profile_dataset(Connector(baseline_dsn or dsn), "t", None, sample_rows=10_000)
    db.add(
        Profile(
            dataset_id=ds.id,
            row_count=prof["row_count"],
            sampled_rows=prof["sampled_rows"],
            columns=prof["columns"],
            table_facts=prof["table_facts"],
        )
    )
    chk = Check(
        dataset_id=ds.id, name="drift", check_type="distribution_drift",
        column_name=column, params=validate_check("distribution_drift", column, params),
        severity="warn", status="active",
    )
    db.add(chk)
    db.commit()
    return CheckContext(
        connector=Connector(dsn), table="t", schema=None, column=column,
        params=chk.params, db=db, check_id=chk.id,
    )


@pytest.fixture(scope="module")
def drift_tmp() -> Path:
    # pytest's tmp_path basetemp is unwritable on this machine (OneDrive/Temp ACLs);
    # mirror conftest's own mkdtemp approach instead.
    return Path(tempfile.mkdtemp(prefix="dqsentinel-drift-"))


@pytest.fixture
def app_db():
    init_db()
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


def test_drift_numeric_stable_passes(app_db, drift_tmp):
    rng = np.random.default_rng(1)
    base = list(rng.normal(0, 1, 4000))
    cur = list(np.random.default_rng(2).normal(0, 1, 4000))
    base_dsn = _make_source(drift_tmp, "num_base", {"v": base})
    cur_dsn = _make_source(drift_tmp, "num_cur_stable", {"v": cur})
    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.metrics["method"] == "psi"
    assert r.metrics["score"] < 0.1, r.metrics
    assert r.violation_count == 0
    assert r.metrics["bins"] and "baseline_profile_id" in r.metrics


def test_drift_numeric_shift_fails(app_db, drift_tmp):
    base = list(np.random.default_rng(1).normal(0, 1, 4000))
    cur = list(np.random.default_rng(3).normal(3, 1, 4000))  # mean shifted +3σ
    base_dsn = _make_source(drift_tmp, "num_base2", {"v": base})
    cur_dsn = _make_source(drift_tmp, "num_cur_shift", {"v": cur})
    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.metrics["score"] > 0.2, r.metrics
    assert r.violation_count == 1
    assert len(r.metrics["bins"]) >= 5  # decile bins present in metrics
    assert "PSI" in r.detail and "baseline profile" in r.detail


def test_drift_categorical_stable_passes(app_db, drift_tmp):
    base = (["a"] * 600) + (["b"] * 300) + (["c"] * 100)
    cur = (["a"] * 590) + (["b"] * 305) + (["c"] * 105)
    base_dsn = _make_source(drift_tmp, "cat_base", {"v": base})
    cur_dsn = _make_source(drift_tmp, "cat_cur_stable", {"v": cur})
    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.metrics["kind"] == "categorical"
    assert r.violation_count == 0, r.metrics
    assert any(b["category"] == "__other__" for b in r.metrics["bins"])


def test_drift_categorical_vanished_category_fails(app_db, drift_tmp):
    base = (["a"] * 400) + (["b"] * 400) + (["c"] * 200)
    cur = (["a"] * 990) + (["d"] * 10)  # b & c vanished, new 'd' appears
    base_dsn = _make_source(drift_tmp, "cat_base2", {"v": base})
    cur_dsn = _make_source(drift_tmp, "cat_cur_vanish", {"v": cur})
    ctx = _drift_ctx(app_db, cur_dsn, "v", {"method": "psi"}, baseline_dsn=base_dsn)
    r = run_check_type(ctx, "distribution_drift")
    assert r.violation_count == 1, r.metrics
    assert r.metrics["score"] > 0.2


def test_drift_no_profile_passes_with_message(app_db, drift_tmp):
    # build a context whose dataset has NO profile row
    dsn = _make_source(drift_tmp, "noprof", {"v": [1.0, 2.0, 3.0, 4.0]})
    conn = Connection(name="c-noprof", kind="sqlite", dsn=dsn)
    app_db.add(conn)
    app_db.flush()
    ds = Dataset(connection_id=conn.id, schema_name=None, table_name="t")
    app_db.add(ds)
    app_db.flush()
    chk = Check(
        dataset_id=ds.id, name="d", check_type="distribution_drift", column_name="v",
        params={"method": "psi"}, severity="warn", status="active",
    )
    app_db.add(chk)
    app_db.commit()
    ctx = CheckContext(
        connector=Connector(dsn), table="t", schema=None, column="v",
        params={"method": "psi"}, db=app_db, check_id=chk.id,
    )
    r = run_check_type(ctx, "distribution_drift")
    assert r.violation_count == 0
    assert "no baseline profile" in r.detail


def test_drift_ks_first_run_captures_then_shift_fails(app_db, drift_tmp):
    base = list(np.random.default_rng(1).normal(0, 1, 3000))
    base_dsn = _make_source(drift_tmp, "ks_base", {"v": base})
    ctx = _drift_ctx(app_db, base_dsn, "v", {"method": "ks", "threshold": 0.05})

    # First run: no prior sample -> baseline captured, passes.
    r1 = run_check_type(ctx, "distribution_drift")
    assert r1.violation_count == 0
    assert r1.metrics["note"] == "baseline captured"
    assert 0 < len(r1.metrics["drift_sample"]) <= 2000

    # Persist that run (the runner normally does this) so the next run sees a prior.
    dataset_id = app_db.get(Check, ctx.check_id).dataset_id
    app_db.add(
        CheckRun(
            check_id=ctx.check_id, dataset_id=dataset_id,
            status="pass", violation_count=0, metrics=dict(r1.metrics),
        )
    )
    app_db.commit()

    # Second run: point the SAME check at a shifted table -> KS should fail.
    shifted_dsn = _make_source(
        drift_tmp, "ks_shift", {"v": list(np.random.default_rng(9).normal(4, 1, 3000))}
    )
    ctx2 = CheckContext(
        connector=Connector(shifted_dsn), table="t", schema=None, column="v",
        params={"method": "ks", "threshold": 0.05}, db=app_db, check_id=ctx.check_id,
    )
    r2 = run_check_type(ctx2, "distribution_drift")
    assert r2.metrics["prior_n"] >= 2
    assert r2.metrics["score"] <= 0.05  # p-value tiny
    assert r2.violation_count == 1
