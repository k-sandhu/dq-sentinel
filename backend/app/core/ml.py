"""ML outlier detection: IsolationForest over numeric columns.

Used by the `ml_outlier` check type. Deterministic (fixed random_state) so reruns
on unchanged data produce stable results.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

# Name tokens that mark a column as an identifier or a code rather than a measurement.
# A zone id, a vendor id or a payment type is numeric but has no magnitude: feeding it
# to IsolationForest makes "row with a rare id" look like a data-quality problem (#263).
ID_NAME_TOKENS = frozenset(
    {"id", "ids", "key", "keys", "code", "codes", "sku", "uuid", "guid", "type", "status",
     "no", "nbr"}
)
# Splits both snake_case and camelCase: DOLocationID -> do / location / id.
_NAME_TOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|[0-9]+")
# Share of distinct values above which an integer column is treated as a surrogate key.
_ID_DISTINCT_PCT = 0.98


def looks_like_identifier_name(name: str) -> bool:
    """Name-only test for "this column is an identifier or a code, not a measurement".

    Tokenised, never substring: `paid_amount` and `humidity` both contain "id" but
    neither has it as a token, while `DOLocationID`, `order_id` and `payment_type` do.
    A false positive costs a dropped feature, so the token list stays narrow and the
    callers pair it with cardinality / profile evidence for the ambiguous cases.
    """
    return any(tok.lower() in ID_NAME_TOKENS for tok in _NAME_TOKEN_RE.findall(name or ""))


def _is_temporal(series: pd.Series) -> bool:
    """True for datetime/timedelta columns, including object columns holding datetimes.

    `pd.to_numeric` on a datetime column succeeds — it yields epoch integers, 100%
    coercible — so without this test the auto-select below silently turned timestamps
    into features and the newest/oldest rows scored as outliers (#263)."""
    if pd.api.types.is_datetime64_any_dtype(series) or pd.api.types.is_timedelta64_dtype(series):
        return True
    if series.dtype == object:
        sample = series.dropna().head(20)
        return len(sample) > 0 and all(
            isinstance(x, (datetime, date, pd.Timestamp, pd.Timedelta)) for x in sample
        )
    return False


def _is_row_identifier(name: str, series: pd.Series, n_rows: int) -> bool:
    """True for a near-unique integer column that is either named like an id or runs
    monotonically — i.e. a surrogate key. Requiring integrality keeps continuous
    measurements (which are also near-unique on a small sample) as features."""
    if n_rows < 20:
        return False
    values = series.dropna()
    if not len(values) or values.nunique() / n_rows < _ID_DISTINCT_PCT:
        return False
    arr = values.to_numpy(dtype=float)
    if not np.all(np.mod(arr, 1) == 0):
        return False
    return looks_like_identifier_name(name) or bool(values.is_monotonic_increasing)


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
    # An explicit `columns` list is the user's decision and is taken verbatim; the
    # filtering below only applies to columns this function picked itself.
    auto = not columns
    if columns:
        numeric = df[[c for c in columns if c in df.columns]].apply(pd.to_numeric, errors="coerce")
    else:
        numeric = df.select_dtypes(include=[np.number]).copy()
        # also pick up numeric-looking object columns (but not datetimes: see _is_temporal)
        for col in df.columns:
            if col not in numeric.columns and not _is_temporal(df[col]):
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
    if auto:
        # Surrogate keys are numeric and never constant, so they survive every filter
        # above and then dominate the forest — an auto-incrementing id makes the newest
        # and oldest rows look anomalous (#263).
        ids = [c for c in numeric.columns if _is_row_identifier(str(c), numeric[c], len(numeric))]
        if ids:
            numeric = numeric.drop(columns=ids)
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
