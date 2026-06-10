"""End-to-end smoke test against a RUNNING API + the bundled sample database.

Usage:
    python data/generate_sample_data.py            # once
    uvicorn app.main:app --port 8000 --app-dir backend   # in another terminal
    python scripts/e2e_smoke.py [--base http://localhost:8000]

Exercises the full workflow: login -> connection -> register tables -> profile
-> knowledge -> generate checks -> activate -> run -> exceptions -> triage ->
custom SQL check -> ML outlier check -> dashboard. Exits non-zero on failure.
"""

import argparse
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "samples" / "shopdb.sqlite"

passed = 0


def ok(label: str, condition: bool, detail: str = "") -> None:
    global passed
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        sys.exit(f"Smoke test failed at: {label} {detail}")
    passed += 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument(
        "--dsn",
        default=None,
        help="DSN for the sample DB AS SEEN BY THE API. Defaults to the local repo path; "
        "for the docker stack use sqlite:////data/samples/shopdb.sqlite",
    )
    args = parser.parse_args()
    base = f"{args.base}/api/v1"

    if args.dsn:
        dsn = args.dsn
    else:
        if not SAMPLE.exists():
            sys.exit(f"Sample DB missing — run: python data/generate_sample_data.py (expected {SAMPLE})")
        dsn = f"sqlite:///{SAMPLE.as_posix()}"

    c = httpx.Client(base_url=base, timeout=180)

    print("== health & auth ==")
    health = c.get("/health").json()
    ok("health", health["status"] == "ok", f"llm_enabled={health['llm_enabled']}")
    r = c.post("/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    ok("login", r.status_code == 200)
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    print("== connection & registration ==")
    r = c.post("/connections", json={"name": "sample-shop", "dsn": dsn})
    if r.status_code == 409:  # rerun: reuse existing
        conn = next(x for x in c.get("/connections").json() if x["name"] == "sample-shop")
    else:
        ok("create connection", r.status_code == 201)
        conn = r.json()
    r = c.post(f"/connections/{conn['id']}/test")
    ok("test connection", r.json()["ok"], r.json()["message"])
    tables = c.get(f"/connections/{conn['id']}/tables").json()
    names = {t["table_name"] for t in tables}
    ok("introspection", {"orders", "customers", "order_items", "payments", "order_revenue"} <= names)

    r = c.post(
        "/datasets/register",
        json={
            "connection_id": conn["id"],
            "tables": [
                {"table_name": "orders"},
                {"table_name": "customers"},
                {"table_name": "payments"},
            ],
        },
    )
    ok("register datasets", r.status_code == 201)
    datasets = {d["table_name"]: d for d in c.get(f"/datasets?connection_id={conn['id']}").json()}
    orders_id = datasets["orders"]["id"]
    customers_id = datasets["customers"]["id"]
    payments_id = datasets["payments"]["id"]

    print("== profiling ==")
    profile = c.post(f"/datasets/{orders_id}/profile").json()
    ok("profile orders", profile["row_count"] == 30000, f"{profile['row_count']} rows")
    cols = {col["name"]: col for col in profile["columns"]}
    ok("order_id is pk candidate", "order_id" in profile["table_facts"]["pk_candidates"])
    ok("order_date detected temporal", any(t["name"] == "order_date" for t in profile["table_facts"]["temporal_columns"]))
    ok("negative totals visible in profile", cols["total_amount"]["min"] < 0)
    cust_profile = c.post(f"/datasets/{customers_id}/profile").json()
    email_col = next(col for col in cust_profile["columns"] if col["name"] == "email")
    ok("customer email nulls profiled", email_col["null_count"] > 0, f"{email_col['null_count']} nulls")
    ok("email pattern inferred", email_col["patterns"].get("email", 0) > 0.9)
    c.post(f"/datasets/{payments_id}/profile")

    print("== knowledge ==")
    r = c.put(
        f"/datasets/{orders_id}/knowledge",
        json={
            "business_context": "All customer orders; feeds revenue reporting.",
            "known_issues": "total_amount sometimes disagrees with line items; occasional far-future dates.",
            "importance": "critical",
            "freshness_sla_hours": 24,
            "pii_columns": [],
        },
    )
    ok("save knowledge", r.status_code == 200)

    print("== check generation (heuristic fallback works without LLM key) ==")
    gen = c.post("/checks/generate", json={"dataset_id": orders_id, "use_llm": True}).json()
    ok("generated proposals", gen["created"] >= 5, f"{gen['created']} via {gen['mode']}")
    freshness = [x for x in gen["checks"] if x["check_type"] == "freshness"]
    ok("freshness proposal uses 24h SLA", freshness and freshness[0]["params"]["max_age_hours"] == 24)

    print("== activate & run ==")
    results = {}
    for chk in gen["checks"]:
        c.patch(f"/checks/{chk['id']}", json={"status": "active"})
        run = c.post(f"/checks/{chk['id']}/run").json()
        results[f"{chk['check_type']}:{chk['column_name']}"] = run
    fresh_run = results.get("freshness:order_date")
    fresh_metrics = (fresh_run or {}).get("metrics", {})
    ok("freshness FAILS (data is ~30h old, SLA 24h)", bool(fresh_run) and fresh_run["status"] == "fail",
       f"{fresh_metrics.get('age_hours')}h old")
    ok("future-dated rows excluded & reported", fresh_metrics.get("future_rows", 0) > 0,
       f"{fresh_metrics.get('future_rows')} future rows")
    ok("row_count baseline collecting", results.get("row_count_anomaly:None", {}).get("status") == "pass")

    print("== custom SQL check (totals must match line items) ==")
    r = c.post(
        "/checks",
        json={
            "dataset_id": orders_id,
            "name": "orders: total_amount must match sum(line items)",
            "check_type": "custom_sql",
            "severity": "error",
            "params": {
                "sql": (
                    "SELECT o.order_id, o.total_amount, SUM(i.line_total) AS items_total "
                    "FROM orders o JOIN order_items i ON i.order_id = o.order_id "
                    "GROUP BY o.order_id, o.total_amount "
                    "HAVING ABS(o.total_amount - SUM(i.line_total)) > 0.01"
                )
            },
        },
    )
    ok("create custom_sql check", r.status_code == 201, r.text[:120])
    run = c.post(f"/checks/{r.json()['id']}/run").json()
    ok("custom_sql catches mismatched totals", run["status"] == "fail" and run["violation_count"] > 300,
       f"{run['violation_count']} mismatches")

    print("== guard blocks writes ==")
    r = c.post(
        "/checks",
        json={"dataset_id": orders_id, "check_type": "custom_sql", "params": {"sql": "DELETE FROM orders"}},
    )
    ok("write SQL rejected at creation", r.status_code == 422)

    print("== ML outlier check on payments (planted 100x amounts) ==")
    r = c.post(
        "/checks",
        json={
            "dataset_id": payments_id,
            "name": "payments: ML outliers",
            "check_type": "ml_outlier",
            "severity": "info",
            "params": {"contamination": 0.004},
        },
    )
    run = c.post(f"/checks/{r.json()['id']}/run").json()
    ok("ml_outlier flags rows", run["violation_count"] > 0, f"{run['violation_count']} outliers")
    excs = c.get(f"/exceptions?run_id={run['id']}&limit=200").json()
    amounts = [e["row_data"].get("amount", 0) for e in excs]
    ok("planted 100x payment typos among top outliers", any(a and a > 5000 for a in amounts),
       f"max flagged amount={max(amounts) if amounts else 0}")
    ok("outlier scores attached", all(e["outlier_score"] is not None for e in excs))

    print("== exceptions triage ==")
    open_excs = c.get(f"/exceptions?dataset_id={orders_id}&status=open&limit=50").json()
    ok("open exceptions exist for orders", len(open_excs) > 0, f"{len(open_excs)} open")
    ids = [e["id"] for e in open_excs[:3]]
    r = c.post("/exceptions/triage", json={"ids": ids, "status": "expected", "note": "seeded demo issue"})
    ok("bulk triage", r.status_code == 200 and all(e["status"] == "expected" for e in r.json()))

    print("== workbench ==")
    r = c.post(
        "/query/run",
        json={"connection_id": conn["id"], "sql": "SELECT status, COUNT(*) AS n FROM orders GROUP BY 1"},
    )
    ok("workbench query runs", r.status_code == 200 and r.json()["row_count"] >= 4, r.text[:120])
    r = c.post("/query/run", json={"connection_id": conn["id"], "sql": "DROP TABLE orders"})
    ok("workbench rejects writes", r.status_code == 422)
    r = c.get(f"/connections/{conn['id']}/schema")
    ok("schema tree", any(t["table_name"] == "orders" and t["columns"] for t in r.json()))
    r = c.get(f"/connections/{conn['id']}/ddl?table=order_revenue")
    ok("view DDL from database", r.json()["source"] == "database" and "SELECT" in r.json()["ddl"].upper())
    r = c.post("/query/suggest", json={"dataset_id": orders_id})
    sugg = r.json()
    ok("suggested queries", len(sugg["suggestions"]) >= 3, f"mode={sugg['mode']}")
    runnable = sum(
        1
        for s in sugg["suggestions"]
        if c.post("/query/run", json={"connection_id": conn["id"], "sql": s["sql"]}).status_code == 200
    )
    ok("all suggestions runnable", runnable == len(sugg["suggestions"]), f"{runnable}/{len(sugg['suggestions'])}")

    print("== ad-hoc dashboards ==")
    r = c.post("/adhoc-dashboards/generate", json={"dataset_id": orders_id, "focus": "order health"})
    ok("dashboard generated", r.status_code == 201, r.text[:120])
    dash = r.json()
    ok("panels executed", dash["panel_count"] > 0 and all(p["error"] is None for p in dash["panels"]),
       f"{dash['panel_count']} panels via {dash['origin']}")
    r = c.get(f"/adhoc-dashboards/{dash['id']}")
    ok("dashboard reopens with fresh data", r.status_code == 200 and r.json()["panels"][0]["columns"])

    print("== fleet health ==")
    r = c.get("/connections/health")
    ok("fleet health probes", r.status_code == 200 and all("latency_ms" in h for h in r.json()))

    print("== dashboard & rca fallback ==")
    dash = c.get("/dashboard").json()
    ok("dashboard aggregates", dash["datasets"] >= 3 and dash["active_checks"] >= 6 and dash["open_exceptions"] > 0,
       f"{dash['active_checks']} active checks, {dash['open_exceptions']} open exceptions")
    if not health["llm_enabled"]:
        r = c.post("/rca/start", json={"dataset_id": orders_id, "question": "why?"})
        ok("rca returns 503 without LLM key", r.status_code == 503)
    else:
        print("  [SKIP] rca 503 check (LLM key present)")

    print(f"\nAll {passed} smoke checks passed.")


if __name__ == "__main__":
    main()
