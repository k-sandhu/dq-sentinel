"""Deterministic scorecard scoring over app metadata.

This module accepts ORM-like objects and precomputed metadata counts/statuses.
It never opens source connectors or writes to the app DB.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

RunStatus = Literal["pass", "warn", "fail", "error", "unknown"]
Importance = Literal["low", "medium", "high", "critical"]
SloStatus = Literal["met", "at_risk", "breached", "unknown", "disabled"]
SloTargetSource = Literal["explicit", "importance_default", "disabled"]
RollupDimension = Literal["domain", "team", "owner", "importance"]

SEVERITY_WEIGHTS = {"info": 0.5, "warn": 1.0, "error": 2.0}
STATUS_POINTS = {"pass": 1.0, "warn": 0.7, "fail": 0.0, "error": 0.0, "unknown": 0.5}
IMPORTANCE_WEIGHTS = {"critical": 4.0, "high": 3.0, "medium": 2.0, "low": 1.0}
DEFAULT_SLO_TARGETS = {"critical": 98.0, "high": 95.0, "medium": 90.0, "low": 85.0}

OPEN_EXCEPTION_STATUS = "open"
UNASSIGNED_LABEL = "Unassigned"
EXCEPTION_PENALTY_PER_OPEN = 2.0
MAX_EXCEPTION_PENALTY = 30.0


@dataclass(frozen=True)
class DatasetScore:
    dataset_id: int
    table_name: str
    display_name: str
    schema_name: str | None
    domain: str
    team: str
    owner: str
    importance: Importance
    importance_weight: float
    score: float | None
    base_score: float | None
    exception_penalty: float
    slo_target: float | None
    slo_target_source: SloTargetSource
    slo_status: SloStatus
    score_gap: float | None
    active_checks: int
    passing_checks: int = 0
    warning_checks: int = 0
    failing_checks: int = 0
    error_checks: int = 0
    unknown_checks: int = 0
    open_exceptions: int = 0
    score_drivers: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RollupScore:
    dimension: str
    key: str
    label: str
    score: float | None
    slo_target: float | None
    slo_status: SloStatus
    score_gap: float | None
    total_datasets: int
    scored_datasets: int
    unknown_datasets: int
    active_checks: int
    passing_checks: int
    warning_checks: int
    failing_checks: int
    error_checks: int
    unknown_checks: int
    open_exceptions: int
    importance_weight: float
    slo_met: int
    slo_at_risk: int
    slo_breached: int
    slo_unknown: int
    slo_disabled: int


def normalize_importance(value: Any) -> Importance:
    raw = str(value or "medium").strip().lower()
    if raw in IMPORTANCE_WEIGHTS:
        return raw  # type: ignore[return-value]
    return "medium"


def normalize_run_status(value: Any) -> RunStatus:
    raw = str(value or "").strip().lower()
    if raw in ("pass", "warn", "fail", "error"):
        return raw  # type: ignore[return-value]
    return "unknown"


def severity_weight(value: Any) -> float:
    return SEVERITY_WEIGHTS.get(str(value or "error").strip().lower(), SEVERITY_WEIGHTS["error"])


def text_attr(obj: Any, name: str, default: str = "") -> str:
    if obj is None:
        return default
    value = getattr(obj, name, default)
    if value is None:
        return default
    return str(value).strip()


def _number_attr(obj: Any, names: Iterable[str]) -> float | None:
    if obj is None:
        return None
    for name in names:
        value = getattr(obj, name, None)
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= number <= 100:
            return number
    return None


def _slo_enabled(knowledge: Any) -> bool:
    if knowledge is None or not hasattr(knowledge, "slo_enabled"):
        return True
    return bool(knowledge.slo_enabled)


def slo_config(knowledge: Any, importance: Importance) -> tuple[float | None, SloTargetSource]:
    if not _slo_enabled(knowledge):
        return None, "disabled"

    explicit = _number_attr(
        knowledge,
        (
            "slo_target_score",
            "quality_slo_target",
            "scorecard_slo_target",
            "scorecard_target_score",
            "slo_target",
        ),
    )
    if explicit is not None:
        return round(explicit, 2), "explicit"
    return DEFAULT_SLO_TARGETS[importance], "importance_default"


def exception_penalty(open_exceptions: int) -> float:
    if open_exceptions <= 0:
        return 0.0
    return min(MAX_EXCEPTION_PENALTY, open_exceptions * EXCEPTION_PENALTY_PER_OPEN)


def slo_status(
    score: float | None, target: float | None, target_source: SloTargetSource
) -> tuple[SloStatus, float | None]:
    if target_source == "disabled":
        return "disabled", None
    if score is None or target is None:
        return "unknown", None
    gap = round(score - target, 2)
    if gap >= 0:
        return "met", gap
    if gap >= -2:
        return "at_risk", gap
    return "breached", gap


def score_dataset(
    dataset: Any,
    knowledge: Any,
    checks: Iterable[Any],
    latest_status_by_check_id: Mapping[int, str | None],
    open_exception_count: int = 0,
) -> DatasetScore:
    """Score one dataset from active checks and app-metadata exception counts."""

    active_checks = [c for c in checks if text_attr(c, "status") == "active"]
    importance = normalize_importance(getattr(knowledge, "importance", None))
    importance_weight = IMPORTANCE_WEIGHTS[importance]

    counts = {"pass": 0, "warn": 0, "fail": 0, "error": 0, "unknown": 0}
    weighted_points = 0.0
    total_weight = 0.0

    for check in active_checks:
        check_id = int(check.id)
        status = latest_status_by_check_id.get(check_id)
        if status is None:
            status = getattr(check, "last_status", None)
        normalized = normalize_run_status(status)
        counts[normalized] += 1
        weight = severity_weight(getattr(check, "severity", None))
        total_weight += weight
        weighted_points += weight * STATUS_POINTS[normalized]

    base_score: float | None
    final_score: float | None
    penalty = 0.0
    if not active_checks or total_weight <= 0:
        base_score = None
        final_score = None
    else:
        base_score = round((weighted_points / total_weight) * 100, 2)
        penalty = exception_penalty(open_exception_count)
        final_score = round(max(0.0, min(100.0, base_score - penalty)), 2)

    target, target_source = slo_config(knowledge, importance)
    status, gap = slo_status(final_score, target, target_source)
    table_name = text_attr(dataset, "table_name")
    display_name = text_attr(dataset, "display_name") or table_name

    return DatasetScore(
        dataset_id=int(dataset.id),
        table_name=table_name,
        display_name=display_name,
        schema_name=getattr(dataset, "schema_name", None),
        domain=text_attr(knowledge, "domain"),
        team=text_attr(knowledge, "team"),
        owner=text_attr(knowledge, "owner"),
        importance=importance,
        importance_weight=importance_weight,
        score=final_score,
        base_score=base_score,
        exception_penalty=round(penalty, 2),
        slo_target=target,
        slo_target_source=target_source,
        slo_status=status,
        score_gap=gap,
        active_checks=len(active_checks),
        passing_checks=counts["pass"],
        warning_checks=counts["warn"],
        failing_checks=counts["fail"],
        error_checks=counts["error"],
        unknown_checks=counts["unknown"],
        open_exceptions=max(0, int(open_exception_count)),
        score_drivers={
            "base_score": base_score,
            "exception_penalty": round(penalty, 2),
            "weighted_status_points": round(weighted_points, 4),
            "severity_weight_total": round(total_weight, 4),
            "unknown_checks": counts["unknown"],
            "open_exceptions": max(0, int(open_exception_count)),
        },
    )


def aggregate_scores(
    scores: Iterable[DatasetScore],
    *,
    dimension: str = "global",
    key: str = "",
    label: str = "All datasets",
) -> RollupScore:
    rows = list(scores)
    known = [r for r in rows if r.score is not None]
    known_weight = sum(r.importance_weight for r in known)
    rollup_score = None
    if known and known_weight > 0:
        rollup_score = round(sum((r.score or 0.0) * r.importance_weight for r in known) / known_weight, 2)

    target_rows = [r for r in rows if r.slo_target is not None and r.slo_status != "disabled"]
    target_weight = sum(r.importance_weight for r in target_rows)
    rollup_target = None
    if target_rows and target_weight > 0:
        rollup_target = round(
            sum((r.slo_target or 0.0) * r.importance_weight for r in target_rows) / target_weight,
            2,
        )

    rollup_status, gap = slo_status(rollup_score, rollup_target, "importance_default")
    if rows and not target_rows and all(r.slo_status == "disabled" for r in rows):
        rollup_status, gap = "disabled", None

    return RollupScore(
        dimension=dimension,
        key=key,
        label=label,
        score=rollup_score,
        slo_target=rollup_target,
        slo_status=rollup_status,
        score_gap=gap,
        total_datasets=len(rows),
        scored_datasets=len(known),
        unknown_datasets=len(rows) - len(known),
        active_checks=sum(r.active_checks for r in rows),
        passing_checks=sum(r.passing_checks for r in rows),
        warning_checks=sum(r.warning_checks for r in rows),
        failing_checks=sum(r.failing_checks for r in rows),
        error_checks=sum(r.error_checks for r in rows),
        unknown_checks=sum(r.unknown_checks for r in rows),
        open_exceptions=sum(r.open_exceptions for r in rows),
        importance_weight=round(sum(r.importance_weight for r in rows), 2),
        slo_met=sum(1 for r in rows if r.slo_status == "met"),
        slo_at_risk=sum(1 for r in rows if r.slo_status == "at_risk"),
        slo_breached=sum(1 for r in rows if r.slo_status == "breached"),
        slo_unknown=sum(1 for r in rows if r.slo_status == "unknown"),
        slo_disabled=sum(1 for r in rows if r.slo_status == "disabled"),
    )


def rollup_key(score: DatasetScore, dimension: RollupDimension) -> tuple[str, str]:
    if dimension == "importance":
        return score.importance, score.importance
    value = getattr(score, dimension)
    if not value:
        return "", UNASSIGNED_LABEL
    return value, value


def rollup_scores(scores: Iterable[DatasetScore], dimension: RollupDimension) -> list[RollupScore]:
    groups: dict[str, tuple[str, list[DatasetScore]]] = {}
    for score in scores:
        key, label = rollup_key(score, dimension)
        groups.setdefault(key, (label, []))[1].append(score)

    rows = [
        aggregate_scores(group_scores, dimension=dimension, key=key, label=label)
        for key, (label, group_scores) in groups.items()
    ]
    return sorted(
        rows,
        key=lambda r: ((r.score is None), r.score if r.score is not None else 101, r.label.lower()),
    )


def sort_datasets_for_attention(scores: Iterable[DatasetScore]) -> list[DatasetScore]:
    return sorted(
        scores,
        key=lambda s: (
            s.score is None,
            s.score if s.score is not None else 101.0,
            -s.open_exceptions,
            s.display_name.lower(),
        ),
    )
