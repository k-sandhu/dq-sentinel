import numpy as np
import pandas as pd

from app.core.ml import detect_outliers, zscore_outliers


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
