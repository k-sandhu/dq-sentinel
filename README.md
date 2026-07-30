# DQ Sentinel

[![CI](https://github.com/k-sandhu/dq-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/k-sandhu/dq-sentinel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**LLM-native data-quality monitoring for enterprise tables and views.** Connect a database,
let the system profile your data and propose checks, run them on a schedule, triage the
exceptions in a Metabase-style UI — and when something breaks, hand it to an agentic
root-cause analyst that investigates with read-only SQL and reports back with evidence.

## What it does

1. **Connect** — register SQLite / DuckDB / PostgreSQL / MySQL / SQL Server / Snowflake /
   BigQuery / Trino / ClickHouse sources (always opened **read-only** where the engine allows;
   every query passes a SQL safety guard that allows only single SELECT/WITH statements).
   Non-core drivers are optional extras: `pip install "dqsentinel[snowflake]"` etc.
2. **Profile** — per-column stats pushed down as SQL aggregates plus sampled quantiles,
   string-format inference (email/uuid/url/date), primary-key and freshness candidates.
3. **Generate checks** — a deterministic heuristic engine always works; with an LLM key
   configured, the model proposes sharper checks using the profile **plus the table
   knowledge you record** (business context, known issues, SLAs, PII columns). Optionally a
   bounded **exploration agent** first writes its own SQL to learn the data — distributions,
   cross-column consistency, orphans — before proposing.
4. **Run on schedule** — checks carry interval or cron schedules; a worker process claims due
   checks (multi-worker safe on Postgres) and records runs with metrics history.
5. **Triage exceptions** — failed runs capture violating rows. Mark them
   acknowledged / expected / resolved / muted with notes; "expected" markings become
   institutional memory.
6. **ML outlier detection** — IsolationForest over numeric columns is a first-class check
   type; flagged rows land in the same exception workflow with anomaly scores.
7. **Root-cause analysis** — an agent reproduces the failure, segments and time-boxes the bad
   rows, queries related tables, and submits a structured report: likely cause + confidence,
   hypotheses marked supported/refuted/inconclusive, evidence items carrying the actual SQL and
   its finding, and recommended actions. The full SQL transcript is shown in the UI.

Around that core loop the platform also ships **lineage** (table-level graph parsed from view
SQL, with a check-health overlay), a guarded **SQL workbench** (schema sidebar, history, saved
queries, CSV export), **custom and LLM-generated dashboards**, an **assistant chat** agent, a
one-click **data catalog**, and the governance surfaces teams ask for next: quality
**scorecards**, **SLA** tracking, **incidents**, **data contracts**, **monitor packs**, an
**audit log**, global search, a read-only **status page**, and **notifications** (Slack, email,
generic webhook, Teams, PagerDuty, Jira, ServiceNow).

Without an LLM key everything still works — generation falls back to heuristics and the agentic
features explain what they need. PII columns flagged in table knowledge are redacted from every
prompt and tool result.

## Quickstart (local)

Prereqs: Python ≥3.12, Node ≥20.

```powershell
# Windows
.\scripts\dev.ps1          # venv (outside OneDrive), deps, sample data, .env
# macOS/Linux
./scripts/dev.sh
```

Then in three terminals (exact commands are printed by the script):

```bash
uvicorn app.main:app --reload --port 8000 --app-dir backend   # 1. API
cd backend && python -m app.worker                            # 2. scheduler worker
cd frontend && npm run dev                                    # 3. UI -> http://localhost:5173
```

Sign in with `admin@example.com` / `admin123`, add a connection to the bundled sample
(`sqlite:///<repo>/samples/shopdb.sqlite` — ~2,600 seeded issues across 18 categories,
documented in `samples/ISSUES.md`), register the tables, hit **Profile now**, then
**Generate checks**. A real public dataset is one command away:
`python data/download_public_data.py` (NYC taxi → DuckDB).

To enable the AI features, point the provider-agnostic LLM layer at any model: set
`ANTHROPIC_API_KEY` in `.env` for the native Anthropic API (model defaults to
`claude-opus-4-8`), or `DQ_LLM_API_KEY` + `DQ_LLM_MODEL` for any OpenAI-compatible endpoint
(OpenRouter by default; `DQ_LLM_BASE_URL` points at vLLM/Ollama/Together/your own gateway).

### Quickstart (Docker)

```bash
docker compose up --build    # UI on http://localhost:3000, Postgres-backed
```

### Verify an install

```bash
python scripts/e2e_smoke.py   # 38 assertions over the live workflow, against a running API
```

## Architecture

```
┌──────────────┐   /api/v1      ┌───────────────────────────────┐
│  frontend/   │ ─────────────► │  backend/  FastAPI            │
│  React 19    │                │  ├─ api/         routers      │
│  Vite 7      │                │  ├─ core/        profiler,    │
└──────────────┘                │  │   check registry, runner,  │
                                │  │   IsolationForest          │
┌──────────────┐   claims due   │  ├─ llm/         check-gen,   │
│ app.worker   │ ◄────────────► │  │   explorer, RCA agents     │
│ (scheduler)  │                │  ├─ connectors/  read-only +  │
└──────────────┘                │  │   SQL safety guard         │
        │                       └────────────┬──────────────────┘
        ▼                                    ▼
  source databases                  app DB (SQLite dev /
  9 engines via dialect registry    PostgreSQL prod)
  (sqlite · duckdb · postgres · mysql · mssql ·
   snowflake · bigquery · trino · clickhouse)
```

- **Backend**: FastAPI + SQLAlchemy 2.0 + Pydantic v2, JWT auth with viewer/editor/admin
  roles, app metadata on SQLite (dev) or PostgreSQL (`DQ_DATABASE_URL`).
- **Check types** (14, pluggable registry — `backend/app/core/check_types.py`): `not_null`,
  `unique`, `accepted_values`, `range`, `string_length`, `regex_match`, `schema_contract`,
  `freshness` (robust to future-dated rows), `row_count_min`, `row_count_anomaly`
  (self-baselining), `custom_sql`, `ml_outlier`, `distribution_drift` (PSI/KS vs the profiling
  baseline), `schema_change`.
- **LLM layer**: provider-agnostic (`app/llm/providers.py`) — the native Anthropic API or any
  OpenAI-compatible endpoint, including local models; no SDK is imported outside that module.
  Structured-output check generation is validated against the registry; explorer & RCA are
  bounded tool-use loops whose every query passes the same read-only guard and row limits as
  human queries.
- **Frontend**: React 19, react-router 7, TanStack Query 5, recharts — no UI kit, ~1,700 lines
  of hand-rolled Metabase-ish CSS.

## Repo guide

| Path | What |
|---|---|
| [AGENTS.md](AGENTS.md) | **Start here** — dev setup, conventions, safety rails, OneDrive gotchas. `CLAUDE.md` symlinks to it. |
| [docs/getting-started.md](docs/getting-started.md) | First sign-in → first triaged exception, as a product walkthrough |
| `backend/app/core/check_types.py` | Check registry + how to add a type |
| `backend/app/llm/` | `providers.py` (the provider abstraction), check generation, exploration agent, RCA agent, chat agent, prompts |
| `backend/app/connectors/safety.py` | The SQL guard everything goes through |
| `backend/app/connectors/dialects.py` | 9-engine dialect registry: schemes, read-only enforcement, optional drivers, DDL catalog queries |
| `backend/app/core/lineage.py` | sqlglot view parsing → lineage graph with check-health overlay |
| `data/` | Sample-data generator + public-dataset downloader |
| `scripts/e2e_smoke.py` | Full-workflow smoke test |

## Security model (v0.1)

- Sources opened read-only at the driver level **and** statement-guarded (single SELECT/WITH,
  keyword denylist, a denylist of read-side functions that reach the filesystem/network/OS,
  forced row limits) — including all LLM-authored SQL.
- JWT auth (HS256), bcrypt passwords, role-gated mutations (`viewer`/`editor`/`admin`) plus
  per-connection grants that narrow a role to specific sources; bootstrap admin seeded once,
  and `DQ_ENV=prod` refuses to boot on the default secret key or admin password.
- Privileged and state-changing actions (users, grants, connections, checks, triage, knowledge,
  contracts, SLAs) are recorded in an append-only **audit log** with an admin-only viewer in
  Settings.
- PII redaction in LLM prompts/tool results based on per-table knowledge.
- Known gaps tracked as issues: DSN encryption at rest (#24), SSO/OIDC (#26).

## Roadmap

Open issues track the hardening path: DSN encryption at rest (#24), distributed execution at
scale (#25), OIDC + granular RBAC (#26), learning from triage to auto-suppress expected
exceptions (#31), and lineage-aware RCA (#32) — plus the larger lanes (quality gates,
checks-as-code, reconciliation, BI lineage, PII classification). See the
[issue tracker](https://github.com/k-sandhu/dq-sentinel/issues), the
[roadmap issue](https://github.com/k-sandhu/dq-sentinel/issues/254), and the
[project board](https://github.com/users/k-sandhu/projects/4).

## License

MIT
