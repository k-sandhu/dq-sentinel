"""ML outlier detection: IsolationForest over numeric columns.

Used by the `ml_outlier` check type. Deterministic (fixed random_state) so reruns
on unchanged data produce stable results.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


@dataclass
class OutlierResult:
    indices: list[int]  # positional indices into the input frame
    scores: list[float]  # anomaly score per flagged row (higher = more anomalous)
    features: list[str]
    rows_scored: int
    threshold: float


def detect_outliers(
    df: pd.DataFrame,
    columns: list[str] | None = None,
    contamination: float = 0.005,
    max_flagged: int = 200,
    random_state: int = 42,
) -> OutlierResult:
    if columns:
        numeric = df[[c for c in columns if c in df.columns]].apply(pd.to_numeric, errors="coerce")
    else:
        numeric = df.select_dtypes(include=[np.number])
        # also pick up numeric-looking object columns
        for col in df.columns:
            if col not in numeric.columns:
                coerced = pd.to_numeric(df[col], errors="coerce")
                if coerced.notna().mean() > 0.95:
                    numeric[col] = coerced

    # ±inf (e.g. Postgres 'Infinity'::float8, or derived/ratio columns) is not NaN,
    # so it survives the median-fill below and then makes StandardScaler /
    # IsolationForest raise "Input contains infinity". Treat it as missing.
    numeric = numeric.replace([np.inf, -np.inf], np.nan)
    numeric = numeric.dropna(axis=1, how="all")
    # constant columns carry no signal
    numeric = numeric.loc[:, numeric.nunique(dropna=True) > 1]
    if numeric.shape[1] == 0 or len(numeric) < 50:
        return OutlierResult([], [], list(numeric.columns), len(numeric), 0.0)

    filled = numeric.fillna(numeric.median(numeric_only=True))
    X = StandardScaler().fit_transform(filled.values)

    contamination = min(max(contamination, 1e-4), 0.5)
    forest = IsolationForest(
        n_estimators=200, contamination=contamination, random_state=random_state, n_jobs=-1
    )
    labels = forest.fit_predict(X)
    scores = -forest.score_samples(X)  # higher = more anomalous

    flagged = np.where(labels == -1)[0]
    if len(flagged) > max_flagged:
        flagged = flagged[np.argsort(scores[flagged])[::-1][:max_flagged]]
    flagged = flagged[np.argsort(scores[flagged])[::-1]]

    threshold = float(np.min(scores[flagged])) if len(flagged) else float(np.max(scores))
    return OutlierResult(
        indices=[int(i) for i in flagged],
        scores=[round(float(scores[i]), 4) for i in flagged],
        features=list(numeric.columns),
        rows_scored=len(numeric),
        threshold=round(threshold, 4),
    )


def zscore_outliers(series: pd.Series, sigma: float = 4.0) -> tuple[list[int], list[float]]:
    """Simple per-column fallback used in tests and single-column checks."""
    s = pd.to_numeric(series, errors="coerce")
    mean, std = s.mean(), s.std()
    if not std or np.isnan(std):
        return [], []
    z = ((s - mean) / std).abs()
    idx = z[z > sigma].sort_values(ascending=False).index
    return [int(i) for i in idx], [round(float(z[i]), 2) for i in idx]
