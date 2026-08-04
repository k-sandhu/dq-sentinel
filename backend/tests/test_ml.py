from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.core.ml import detect_outliers, looks_like_identifier_name, zscore_outliers


def _frame_with_outliers() -> tuple[pd.DataFrame, list[int]]:
    rng = np.random.default_rng(7)
    n = 600
    df = pd.DataFrame(
        {
            "amount": rng.normal(100, 8, n),
            "quantity": rng.normal(3, 0.5, n),
            "label": ["ok"] * n,  # non-numeric column must be ignored
        }
    )
    planted = [50, 200, 400]
    for i in planted:
        df.loc[i, "amount"] = 5000.0
        df.loc[i, "quantity"] = 80.0
    return df, planted


def test_detect_outliers_tolerates_infinity():
    # ±inf (e.g. Postgres 'Infinity'::float8) is not NaN and used to raise inside
    # StandardScaler/IsolationForest; it must be treated as missing, not crash.
    df, planted = _frame_with_outliers()
    df.loc[10, "amount"] = np.inf
    df.loc[11, "quantity"] = -np.inf
    result = detect_outliers(df, contamination=0.01)  # must not raise
    assert result.rows_scored == len(df)
    assert set(planted) <= set(result.indices)


def test_isolation_forest_finds_planted():
    df, planted = _frame_with_outliers()
    result = detect_outliers(df, contamination=0.01)
    assert set(planted) <= set(result.indices)
    assert result.rows_scored == len(df)
    assert set(result.features) == {"amount", "quantity"}
    # scores sorted descending, planted rows are the most anomalous
    assert result.scores == sorted(result.scores, reverse=True)
    assert set(result.indices[:3]) == set(planted)


def test_small_or_constant_data_is_safe():
    tiny = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    assert detect_outliers(tiny).indices == []
    constant = pd.DataFrame({"a": [5.0] * 100})
    assert detect_outliers(constant).indices == []


def test_zscore():
    s = pd.Series([10.0] * 99 + [10_000.0])
    idx, scores = zscore_outliers(s, sigma=4.0)
    assert idx == [99]
    assert scores[0] > 4


# ------------------------------------------------------------------ #263 feature hygiene


def test_looks_like_identifier_name_is_tokenised_not_substring():
    for name in ("id", "order_id", "customer_key", "product_code", "DOLocationID",
                 "VendorID", "payment_type", "order_no", "record_uuid"):
        assert looks_like_identifier_name(name), name
    # 'paid'/'humid'/'video' contain "id" but never as a token — dropping a real
    # measurement is the expensive failure mode, so the match must stay tokenised.
    for name in ("paid_amount", "humidity", "humid", "video_seconds", "total_amount",
                 "trip_distance", "passenger_count"):
        assert not looks_like_identifier_name(name), name


def test_auto_select_drops_identifier_and_datetime_columns():
    # An auto-incrementing id and a timestamp are numeric (pd.to_numeric turns a
    # datetime into epoch ints, 100% coercible) so they used to become IsolationForest
    # features, and the newest/oldest rows scored as "outliers" (#263).
    df, planted = _frame_with_outliers()
    n = len(df)
    df.insert(0, "order_id", range(1, n + 1))
    df["created_at"] = pd.date_range("2024-01-01", periods=n, freq="min")
    df["ingested_at"] = [datetime(2024, 1, 1) + timedelta(minutes=i) for i in range(n)]

    result = detect_outliers(df, contamination=0.01)

    assert set(result.features) == {"amount", "quantity"}
    assert set(planted) <= set(result.indices)
    # the id extremes are no longer the most anomalous rows
    assert 0 not in result.indices
    assert n - 1 not in result.indices


def test_explicit_columns_stay_authoritative():
    # The filtering above is only for columns detect_outliers picked itself: when the
    # analyst names the features, we use exactly those.
    df, _planted = _frame_with_outliers()
    df.insert(0, "order_id", range(1, len(df) + 1))
    result = detect_outliers(df, columns=["order_id", "amount"], contamination=0.01)
    assert set(result.features) == {"order_id", "amount"}
