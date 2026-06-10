"""Ad-hoc dashboards: heuristic panel generation + panel execution.

A panel is {title, description, sql, viz}. SQL is guarded and row-capped at
execution; failures are captured per-panel so one bad query never sinks the
dashboard.
"""

import time
from typing import Any

from app.connectors.sa import Connector
from app.connectors.safety import guard_sql
from app.core.profiler import jsonable
from app.core.suggest import day_expr

PANEL_ROW_CAP = 500
VIZ_TYPES = {"number", "bar", "line", "area", "pie", "table"}


def heuristic_panels(connector: Connector, ref: str, profile: dict[str, Any] | None) -> list[dict]:
    cols = (profile or {}).get("columns", [])
    facts = (profile or {}).get("table_facts", {})
    panels: list[dict] = [
        {
            "title": "Total rows",
            "description": "",
            "sql": f"SELECT COUNT(*) AS total_rows FROM {ref}",
            "viz": {"type": "number", "x": None, "y": "total_rows"},
        }
    ]

    temporal = facts.get("temporal_columns") or []
    if temporal:
        tq = connector.quote(temporal[0]["name"])
        panels.append(
            {
                "title": "Rows per day (last 45 loaded days)",
                "description": f"by {temporal[0]['name']}",
                "sql": (
                    f"SELECT {day_expr(connector.kind, tq)} AS day, COUNT(*) AS n\n"
                    f"FROM {ref}\nGROUP BY 1\nORDER BY 1 DESC\nLIMIT 45"
                ),
                "viz": {"type": "line", "x": "day", "y": "n"},
            }
        )

    categoricals = [c for c in cols if c["kind"] == "string" and 2 <= c["distinct_count"] <= 20]
    categoricals.sort(key=lambda c: c["distinct_count"])
    for i, c in enumerate(categoricals[:2]):
        q = connector.quote(c["name"])
        panels.append(
            {
                "title": f"Rows by {c['name']}",
                "description": f"{c['distinct_count']} distinct values",
                "sql": f"SELECT {q} AS value, COUNT(*) AS n\nFROM {ref}\nGROUP BY 1\nORDER BY n DESC\nLIMIT 12",
                "viz": {"type": "pie" if i == 1 else "bar", "x": "value", "y": "n"},
            }
        )

    numerics = [
        c for c in cols
        if c["kind"] == "numeric" and c.get("stddev") and isinstance(c.get("min"), (int, float))
        and isinstance(c.get("max"), (int, float)) and c["max"] > c["min"]
    ]
    if numerics:
        c = max(numerics, key=lambda c: c["distinct_count"])
        q = connector.quote(c["name"])
        width = (c["max"] - c["min"]) / 30 or 1
        width = round(width, 6) if width < 1 else round(width)
        panels.append(
            {
                "title": f"Distribution of {c['name']}",
                "description": f"bucket width {width}",
                "sql": (
                    f"SELECT ROUND({q} / {width}) * {width} AS bucket, COUNT(*) AS n\n"
                    f"FROM {ref}\nWHERE {q} IS NOT NULL\nGROUP BY 1\nORDER BY 1\nLIMIT 80"
                ),
                "viz": {"type": "bar", "x": "bucket", "y": "n"},
            }
        )

    nully = [c for c in cols if c["null_pct"] > 0]
    if nully:
        worst = max(nully, key=lambda c: c["null_pct"])
        q = connector.quote(worst["name"])
        panels.append(
            {
                "title": f"Rows missing {worst['name']}",
                "description": f"{worst['null_pct']:.2%} NULL at last profile",
                "sql": f"SELECT COUNT(*) AS null_rows FROM {ref} WHERE {q} IS NULL",
                "viz": {"type": "number", "x": None, "y": "null_rows"},
            }
        )
    return panels[:6]


def normalize_panels(raw_panels: list[dict]) -> list[dict]:
    """Validate guard + viz on (possibly LLM-authored) panels; drop invalid."""
    out = []
    for p in raw_panels:
        try:
            sql = guard_sql(str(p.get("sql") or ""))
        except Exception:  # noqa: BLE001
            continue
        viz = p.get("viz") or {}
        vtype = viz.get("type") if viz.get("type") in VIZ_TYPES else "table"
        out.append(
            {
                "title": str(p.get("title") or "Panel")[:300],
                "description": str(p.get("description") or "")[:500],
                "sql": sql,
                "viz": {"type": vtype, "x": viz.get("x"), "y": viz.get("y")},
            }
        )
        if len(out) >= 8:
            break
    return out


def execute_panels(connector: Connector, panels: list[dict]) -> list[dict]:
    """Run every panel; per-panel errors are reported, not raised."""
    results = []
    for p in panels:
        item = dict(p)
        start = time.perf_counter()
        try:
            res = connector.run_select(p["sql"], limit=PANEL_ROW_CAP)
            item["columns"] = res.columns
            item["rows"] = [[jsonable(v) for v in row] for row in res.rows]
            item["error"] = None
        except Exception as exc:  # noqa: BLE001 - panel-level failure isolation
            item["columns"], item["rows"] = [], []
            item["error"] = f"{type(exc).__name__}: {exc}"
        item["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        results.append(item)
    return results
