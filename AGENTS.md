# AGENTS.md — DQ Sentinel agent harness

This file is the single source of truth for AI agents (and humans) working on this
repository. `CLAUDE.md` is a symlink to this file (with an `@AGENTS.md` import fallback
when symlinks are unavailable). Read this top-to-bottom before making changes.

## What this project is

**DQ Sentinel** is an enterprise data-quality platform:

1. Connect to a data source via SQLAlchemy DSNs — SQLite / DuckDB / PostgreSQL / MySQL /
   SQL Server / Snowflake / BigQuery / Trino / ClickHouse (non-core drivers are optional
   pip extras; see `backend/app/connectors/dialects.py`).
2. Register tables/views as **datasets** and **profile** them (SQL aggregates + sampled stats).
3. **Generate checks** from the profile — heuristic rules always, LLM-proposed rules when a key
   is configured for any supported provider (the LLM can also run a read-only *exploration
   agent* that writes SQL to learn about the data before proposing checks).
4. Run checks **on a schedule** (worker process) and capture **exceptions** (violating rows).
5. Users **triage exceptions** (open → acknowledged / expected / resolved / muted) in a
   Metabase-style UI; "expected" markings feed back into the table knowledge base.
6. **ML outlier detection** (IsolationForest) runs as a first-class check type.
7. A **root-cause-analysis agent** (LLM, tool-use loop) writes read-only SQL against the
   source to investigate failures and produces an evidence-backed report.

```
┌──────────────┐   HTTP/JSON    ┌───────────────────────────────┐
│  frontend/   │ ─────────────► │  backend/  FastAPI (app.main) │
│  React+Vite  │                │  ├─ api/        routers       │
└──────────────┘                │  ├─ core/       profiler,     │
                                │  │   checks, runner, ml       │
┌──────────────┐   polls DB     │  ├─ llm/        check-gen,    │
│ app.worker   │ ◄────────────► │  │   explorer, RCA agent      │
│ (scheduler)  │                │  ├─ connectors/ read-only SQL │
└──────────────┘                │  └─ models.py   app metadata  │
        │                       └───────────────┬───────────────┘
        ▼                                       ▼
  source databases                     app DB (SQLite dev /
  (9 engines, read-only)               PostgreSQL prod)
```

## Repo map

| Path | What lives there |
|---|---|
| `backend/app/main.py` | FastAPI app factory + router mounting + `UnhandledErrorMiddleware` (any unhandled exception becomes a consistent JSON 500 carrying the request id; the traceback goes to the log, never the body) |
| `backend/app/models.py` | All SQLAlchemy ORM models (app metadata DB) |
| `backend/app/schemas.py` | All Pydantic request/response schemas |
| `backend/app/api/` | One router per resource (auth, connections, datasets, checks, runs, exceptions, knowledge, rca, dashboard, custom/adhoc dashboards, lineage, query/workbench, saved queries, contracts, monitors, incidents, scorecards, sla, insights, notifications, audit, catalog, search, status, docs, mcp, chat — `chat` also serves the assistant WebSocket at `/api/v1/chat/ws/{session_id}?token=`) |
| `backend/app/api/_filters.py` | **The LIKE-escaping chokepoint.** Every `?q=` substring filter must build its pattern with `contains_pattern()` and pass `escape=LIKE_ESCAPE` — an unescaped `%`/`_` is a wildcard, so `?q=%` returns every row |
| `backend/app/connectors/` | Source-DB access. **All source SQL must pass `safety.guard_sql()`** — single SELECT/CTE, side-effect keyword denylist, *and* a denylist of read-side functions that reach the filesystem/network/OS (`read_csv`, `load_file`, `postgres_scan`, `xp_cmdshell`, …). `dialects.py` is the 9-engine registry (schemes, read-only enforcement, optional drivers, DDL catalog queries) |
| `backend/app/core/lineage.py` | sqlglot view parsing → table-level lineage graph + check-health overlay |
| `backend/app/core/check_types.py` | Check registry: type → param schema + violation-SQL compiler |
| `backend/app/core/profiler.py` | Profiling engine (SQL aggregates + pandas sample stats) |
| `backend/app/core/runner.py` | Executes a check → `CheckRun` + `ExceptionRecord`s |
| `backend/app/core/scheduler.py` | Due-check claiming loop (run via `python -m app.worker`) |
| `backend/app/core/ml.py` | IsolationForest outlier detection |
| `backend/app/llm/` | `providers.py` (the only place an LLM SDK is imported), check generation, exploration agent, RCA agent, chat agent |
| `backend/tests/` | pytest suite — keep green |
| `frontend/src/pages/` | One file per page; `DatasetDetailPage` is tabbed — 12 tabs, each a route (Profile/Code/Schema/Lineage/Contract/Monitors/Checks/Runs/Exceptions/Dashboards/Knowledge/Root cause); the list is the `TABS` const at the top of the file |
| `frontend/src/api/` | Typed API client (`client.ts`, `types.ts`) — mirror backend schemas here |
| `frontend/src/lib/useUnsavedGuard.ts` | The standard way to guard a dirty editor. Any page holding unsaved analyst typing calls it; it intercepts in-app navigation *and* `beforeunload` and asks via the shared confirm dialog. Do not hand-roll a `beforeunload` listener or call `window.confirm` |
| `data/` | `generate_sample_data.py` (synthetic shop DB with injected DQ issues), `download_public_data.py` (NYC taxi → DuckDB) |
| `scripts/` | `dev.ps1` / `dev.sh` bootstrap helpers |
| `.github/workflows/ci.yml` | CI, on PRs and pushes to `main`. Backend: ruff over `app`/`tests`/`scripts`/`data`, static validation of the shipped configs (compose, Grafana dashboard JSON), pytest. Frontend: typecheck + build, vitest. Jobs and steps get added — **read the workflow** rather than trusting this row |

## Golden rules

1. **Never write to a source database, and never let it read the host.** Every query against a
   user's data source must go through `app/connectors/safety.py: guard_sql()` (single
   SELECT/CTE, side-effect keyword denylist, read-side file/network/OS function denylist,
   forced LIMIT) and connectors open read-only where the driver supports it. This includes
   LLM-authored SQL. A new engine or extension usually means new function names to deny.
2. **Never commit secrets.** Config comes from env vars (see `.env.example`). The app DB and
   sample DBs (`*.sqlite`, `*.duckdb`, `backend/dqsentinel.db`) are gitignored — never force-add them.
3. **Keep frontend and backend decoupled.** The only contract is the JSON API. If you change a
   response schema in `backend/app/schemas.py`, update `frontend/src/api/types.ts` in the same change.
4. **LLM features must degrade gracefully.** With no LLM key configured (see *LLM integration
   notes* — `ANTHROPIC_API_KEY` or `DQ_LLM_API_KEY`), check generation falls back to heuristics
   and explorer/RCA endpoints return 503 with a clear message. Preserve this.
5. **Adding a check type** touches exactly four places: `core/check_types.py` (registry entry +
   compiler), `schemas.py` (if params need validation), `frontend/src/api/types.ts` +
   `frontend/src/lib/checkMeta.ts` (label/description), and a test in `tests/test_checks.py`.
   `CHECK_TYPES` is the single source of truth for how many types exist — count it, don't
   copy a list out of the docs.
6. **Tests must pass before you commit**: `cd backend && pytest`, and `cd frontend && npm run build`
   plus `npm test` (CI runs vitest too).
7. **Reference GitHub issues** in commits (`Refs #N` / `Closes #N`). Track new feature work as issues first (`gh issue create`).
8. **Privacy in prompts:** LLM prompts may include aggregates and ≤25 sample rows. Columns listed
   in a dataset's knowledge `pii_columns` are redacted before being sent. Keep it that way.
9. **App-DB schema changes need an Alembic revision (#23).** Any change to `models.py` must ship
   with a migration in the same change: `cd backend && alembic revision --autogenerate -m "..."`
   (revisions land in `backend/migrations/versions/`, numbered `NNNN_slug.py`), then review it
   (autogen misses some things). `init_db()` runs `upgrade head` at startup (and
   *stamps* a pre-Alembic DB instead of re-applying the baseline). Don't re-introduce a
   `create_all`/ALTER shim. `tests/test_migrations.py` fails if migrations drift from the models.

## Dev setup (Windows-first; POSIX equivalents in parentheses)

Prereqs: Python ≥3.12, Node ≥20, git. Optional: Docker, and an LLM API key (Anthropic, or any
OpenAI-compatible endpoint — see *LLM integration notes*).

```powershell
# 1. Python venv — put it OUTSIDE OneDrive (see OneDrive section below)
python -m venv $env:USERPROFILE\.venvs\dq-sentinel       # (python -m venv ~/.venvs/dq-sentinel)
& $env:USERPROFILE\.venvs\dq-sentinel\Scripts\Activate.ps1   # (source ~/.venvs/dq-sentinel/bin/activate)
pip install -e backend[dev]

# 2. Env
Copy-Item .env.example .env    # then edit; everything has dev defaults

# 3. Sample data (creates samples/shopdb.sqlite with seeded DQ issues)
python data/generate_sample_data.py

# 4. Backend API  →  http://localhost:8000  (docs at /docs)
uvicorn app.main:app --reload --port 8000 --app-dir backend

# 5. Worker (separate terminal; runs scheduled checks)
python -m app.worker            # run from backend/ or with PYTHONPATH=backend

# 6. Frontend  →  http://localhost:5173 (proxies /api to :8000)
cd frontend; npm install; npm run dev
```

First login: `admin@example.com` / `admin123` — seeded from `DQ_BOOTSTRAP_ADMIN_PASSWORD` only
when the user table is empty. **There is no password-change UI**: set that env var before the
first boot, or have an admin `PATCH /api/v1/auth/users/{id}` with `{"password": "..."}`.
Settings → Users covers invite / role / activate / per-connection grants only.

### Standing demo (Docker) — keep it running

The owner reviews by clicking through a live UI. Keep the compose stack up as the standing demo:

```powershell
docker compose up --build -d        # UI on http://localhost:3000 (Postgres-backed, auto-restarts)
docker compose --profile monitoring up -d   # optionally add Prometheus/Grafana/Loki (opt-in profile)
python scripts/e2e_smoke.py --base http://127.0.0.1:8000 --dsn sqlite:////data/samples/shopdb.sqlite
#   ^ seeds connection/datasets/profiles/checks/runs/exceptions (4 slashes = container path)
docker compose up --build -d api worker   # rebuild backend after changes (frontend analogous)
```

Push coherent checkpoints to `main` frequently (CI gates them) rather than batching big drops.

## Commands cheat-sheet

| Task | Command |
|---|---|
| Backend tests | `cd backend && pytest -q` |
| Backend lint | `cd backend && ruff check app tests` |
| New DB migration | `cd backend && alembic revision --autogenerate -m "..."` (then review the file) |
| Apply migrations | `cd backend && alembic upgrade head` (also runs automatically on app startup) |
| Frontend typecheck | `cd frontend && npm run typecheck` |
| Frontend build | `cd frontend && npm run build` (runs typecheck first) |
| Frontend tests | `cd frontend && npm test` (vitest; single file: `npx vitest run <path>`) |
| Regenerate sample data | `python data/generate_sample_data.py --force` |
| Download public dataset | `python data/download_public_data.py` |
| One-shot bootstrap | `scripts/dev.ps1` (PowerShell) / `scripts/dev.sh` |

## Conventions

- **Python**: 3.12+, type hints everywhere, Pydantic v2 idioms (`model_config`, `model_dump()`),
  SQLAlchemy 2.0 style (`Mapped[]`, `mapped_column`). Lint with ruff (config in `backend/pyproject.toml`).
  No pandas in request paths except profiling/ML sampling.
- **TypeScript**: strict mode, function components + hooks, TanStack Query for all server state
  (no manual `useEffect` fetching), react-router v7. Plain CSS in `src/styles.css` — Metabase-ish
  look: brand `#509ee3`, bg `#f9fbfc`, white cards, 8px radii. No UI kit dependencies.
- **Never destroy analyst state silently.** Anything holding unsaved typing guards with
  `lib/useUnsavedGuard.ts`; anything destructive or surprising asks through the shared dialog in
  `components/confirm.tsx` — never `window.confirm`/`window.prompt` (they are unstyled,
  unlabelled, and untestable). Mutations must invalidate the query keys their result changes.
- **User text in a SQL filter is data, not pattern.** Route every `?q=` through
  `api/_filters.py` (`contains_pattern` + `escape=LIKE_ESCAPE`).
- **Commits**: imperative subject, scope prefix (`backend:`, `frontend:`, `data:`, `ci:`), body
  explains why, reference issues.
- **API**: REST-ish under `/api/v1`. Everything except `/api/v1/health` and `/api/v1/auth/login`
  requires a Bearer JWT. Role gates: `viewer` (read), `editor` (mutate checks/triage), `admin`
  (connections/users). The global role is a *ceiling*; per-connection grants narrow it. Any
  router that returns or mutates connection-scoped rows filters through
  `security.visible_connection_ids()` — a new list/detail endpoint that skips it leaks across
  tenants (#72).

## LLM integration notes

- **Provider-agnostic** (`app/llm/providers.py`): `AnthropicProvider` (native API: adaptive
  thinking, structured outputs, MCP connector) and `OpenAICompatProvider` (ANY base_url+key —
  OpenRouter default, vLLM/Ollama/Together/...). Selection via `DQ_LLM_PROVIDER`
  (auto|anthropic|openai|openrouter) + `ANTHROPIC_API_KEY` / `DQ_LLM_API_KEY` +
  `DQ_LLM_BASE_URL` + `DQ_LLM_MODEL`; resolution logic lives in `config.Settings.resolved_llm()`.
- Everything upstream (agent loops, check-gen, suggestions, dashboards) works on the normalized
  `LlmResponse`/history contract — never import an SDK outside `providers.py`. Each provider keeps
  its raw assistant payload in history for fidelity (Anthropic thinking signatures, OpenAI
  tool_call ids) — don't strip `raw`.
- Capability differences are handled inside providers: structured outputs fall back to
  prompt-embedded schemas on endpoints without `response_format`; the MCP connector only attaches
  on the Anthropic path. Test loop changes with the `FakeProvider` in `tests/test_llm_providers.py`.
- All agent SQL goes through the same `guard_sql()` + row-limit path as humans.
- The explorer and RCA agents are bounded loops (max turns, max rows per query) — keep bounds.
- LLM HTTP calls are bounded too: `DQ_LLM_TIMEOUT_SECONDS` (default 90) + `DQ_LLM_MAX_RETRIES`
  on both SDK clients. Keep the timeout below any reverse-proxy read timeout (nginx: 300s).
  OpenAI-compatible structured-output requests fall back to a prompt-embedded schema when the
  endpoint errors — including OpenRouter's 200-with-error-payload (`choices=null`) responses —
  and the fallback is sticky per process. On OpenRouter `response_format` is skipped up front:
  its structured-output emulation can trickle for minutes before failing on large schemas.
- Errors shown to users go through `llm/client.py: safe_user_error()` (chat + RCA): actionable
  config/timeout messages pass through, everything else becomes a generic line while the full
  traceback goes to the server log. Don't put raw exception text in WS events or reports.
- The **assistant chat** (`app/llm/chat_agent.py` + `app/api/chat.py`, frontend `/assistant`) streams
  agent turns over a WebSocket (JWT via `token` query param, editor role; events: status/step/
  message). Tools: dataset overview, recent failures, run_sql, get_table_code, render_chart
  (inline charts reuse the PanelChart viz contract). Messages persist in `chat_messages`; the
  provider-native history is in-process only and is rehydrated from messages after a restart.
  Turn budget: `DQ_LLM_MAX_CHAT_TURNS`. WS proxying: vite `ws: true` in dev, Upgrade headers in
  `frontend/nginx.conf` for docker.
- When changing prompts, keep the JSON output contracts in `app/llm/prompts.py` in sync with the
  parsers next to them; parsers must tolerate markdown-fenced JSON.

## Observability

- Logging: `app/observability.py` — `DQ_LOG_FORMAT=json|text`, request-ID correlation
  (`X-Request-ID` in/out), one structured line per request. Don't `print()`; use module loggers,
  pass structured fields via `extra={"event": ...}`.
- Metrics: prometheus-client. API serves `/metrics` (unauthenticated by design — counts only;
  keep it network-internal in prod); worker exposes `:9100`. Domain metrics: `dq_check_runs_total`,
  `dq_source_queries_total{engine}`, `dq_llm_requests_total{provider,model,outcome}`,
  `dq_llm_tokens_total`, `dq_worker_*`, `dq_sla_attainment{sla}`, `dq_sla_breaches_total{sla}`.
  Label cardinality discipline: route templates, engine
  names, providers — never raw paths/SQL/dataset names.
- Stack (all OSS): Prometheus + Grafana OSS + Loki + Promtail in docker-compose behind the
  opt-in `monitoring` profile (`docker compose --profile monitoring up`); configs under
  `monitoring/`. Grafana on :3001 (admin/admin) auto-provisions the "DQ Sentinel Overview"
  dashboard. New metrics belong on that dashboard (`monitoring/grafana/dashboards/dq-sentinel.json`).

## OneDrive gotchas (this repo may live inside OneDrive on Windows)

- **Symptom: `EPERM`, `WinError 32` (file in use), flaky `npm install`/`pip install`.** OneDrive
  holds file handles while syncing. Fix: retry once; for big installs, pause OneDrive sync or run
  `npm install` again — it is idempotent.
- **Keep the Python venv OUTSIDE OneDrive** (`%USERPROFILE%\.venvs\...`): venvs contain running
  executables that OneDrive loves to lock, and thousands of small files that thrash sync.
- `node_modules/` is gitignored but will still sync (noise, not breakage). Optionally mark the repo
  folder "Always keep on this device" to avoid files-on-demand placeholders breaking watchers.
- **Symlinks** may be unavailable (needs Windows Developer Mode). `CLAUDE.md` is committed as a
  symlink to `AGENTS.md`; if your checkout materializes it as a plain file containing the path or
  an `@AGENTS.md` import line, that is expected — do not "fix" it.
- File watchers (uvicorn `--reload`, Vite) occasionally double-fire on OneDrive; harmless.
- SQLite files under OneDrive can hit transient locks: app DB uses WAL + retries; if a test fails
  with `database is locked`, re-run once before investigating.

## Safety rails for agents working here

- Do not run destructive git commands (`reset --hard`, `push --force`) or delete `samples/` data
  you didn't generate.
- Do not add dependencies casually — both lockfiles/manifests are reviewed; prefer stdlib.
- Do not log or commit data rows from real customer sources; sample data only.
- The seeded admin password is for local dev only — never reuse it in deployment code.
- CI must stay green: if you touch backend and frontend, run both test suites locally first.

## Issue / PR workflow

- Work is tracked in GitHub issues with labels (`backend`, `frontend`, `llm`, `ml`, `infra`,
  `data`, `enhancement`, `bug`) and the **DQ Sentinel** project board (`gh project list --owner k-sandhu`).
- Branch naming: `feat/<slug>`, `fix/<slug>`. Solo-dev pushes to `main` are acceptable for now;
  CI still gates.
- When you finish a feature, close its issue with a commit footer (`Closes #N`) and move the board
  item to Done.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `database is locked` (app DB) | OneDrive/WAL contention — retry; keep worker count at 1 for SQLite |
| `PermissionError: [WinError 5] ... Temp\pytest-of-<user>` at test setup | Windows only: a stale pytest temp root with broken ACLs (concurrent pytest runs can leave one behind). Tests using `tmp_path` error in *setup*, so it looks like 8+ failures with no assertion. Re-run with `pytest --basetemp=<a writable dir>`; delete the stale root when a reboot releases it. Not a code fault — CI (Linux) is unaffected |
| `regex check unsupported` on SQLite | expected: regex checks run via the Python fallback path (chunked fetch) |
| LLM endpoints return 503 | no LLM key resolved (`ANTHROPIC_API_KEY`, or `DQ_LLM_API_KEY` + `DQ_LLM_MODEL`) — heuristic generation still works; `/health` reports `llm_enabled` |
| `npm run dev` proxy errors | backend not running on :8000, or `VITE_API_PROXY` overridden |
| `Cannot find module '@rollup/rollup-win32-x64-msvc'` | npm optional-deps bug (worse under OneDrive): `npm install @rollup/rollup-win32-x64-msvc --no-save` — do NOT add it to package.json (platform-specific; CI runs Linux) |
| Worker runs nothing | checks need `status=active` + a schedule; check `next_run_at` in DB |
| `duckdb.IOException: database is locked` | another process (e.g. a notebook) holds the DuckDB file — DuckDB allows one writer; connectors open read-only, so close the writer |
