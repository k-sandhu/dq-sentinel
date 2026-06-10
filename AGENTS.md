# AGENTS.md — DQ Sentinel agent harness

This file is the single source of truth for AI agents (and humans) working on this
repository. `CLAUDE.md` is a symlink to this file (with an `@AGENTS.md` import fallback
when symlinks are unavailable). Read this top-to-bottom before making changes.

## What this project is

**DQ Sentinel** is an enterprise data-quality platform:

1. Connect to a data source (SQLite / DuckDB / PostgreSQL via SQLAlchemy DSNs).
2. Register tables/views as **datasets** and **profile** them (SQL aggregates + sampled stats).
3. **Generate checks** from the profile — heuristic rules always, LLM-proposed rules when an
   Anthropic API key is configured (the LLM can also run a read-only *exploration agent* that
   writes SQL to learn about the data before proposing checks).
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
  (sqlite/duckdb/postgres)             PostgreSQL prod)
```

## Repo map

| Path | What lives there |
|---|---|
| `backend/app/main.py` | FastAPI app factory + router mounting |
| `backend/app/models.py` | All SQLAlchemy ORM models (app metadata DB) |
| `backend/app/schemas.py` | All Pydantic request/response schemas |
| `backend/app/api/` | One router per resource (auth, connections, datasets, checks, runs, exceptions, knowledge, rca, dashboard) |
| `backend/app/connectors/` | Source-DB access. **All source SQL must pass `safety.guard_sql()`** |
| `backend/app/core/check_types.py` | Check registry: type → param schema + violation-SQL compiler |
| `backend/app/core/profiler.py` | Profiling engine (SQL aggregates + pandas sample stats) |
| `backend/app/core/runner.py` | Executes a check → `CheckRun` + `ExceptionRecord`s |
| `backend/app/core/scheduler.py` | Due-check claiming loop (run via `python -m app.worker`) |
| `backend/app/core/ml.py` | IsolationForest outlier detection |
| `backend/app/llm/` | Anthropic client, check generation, exploration agent, RCA agent |
| `backend/tests/` | pytest suite — keep green |
| `frontend/src/pages/` | One file per page; `DatasetDetailPage` is tabbed (Profile/Checks/Runs/Exceptions/Knowledge/RCA) |
| `frontend/src/api/` | Typed API client (`client.ts`, `types.ts`) — mirror backend schemas here |
| `data/` | `generate_sample_data.py` (synthetic shop DB with injected DQ issues), `download_public_data.py` (NYC taxi → DuckDB) |
| `scripts/` | `dev.ps1` / `dev.sh` bootstrap helpers |
| `.github/workflows/ci.yml` | CI: ruff + pytest + tsc + vite build |

## Golden rules

1. **Never write to a source database.** Every query against a user's data source must go
   through `app/connectors/safety.py: guard_sql()` (single SELECT/CTE, denylist, forced LIMIT)
   and connectors open read-only where the driver supports it. This includes LLM-authored SQL.
2. **Never commit secrets.** Config comes from env vars (see `.env.example`). The app DB and
   sample DBs (`*.sqlite`, `*.duckdb`, `backend/dqsentinel.db`) are gitignored — never force-add them.
3. **Keep frontend and backend decoupled.** The only contract is the JSON API. If you change a
   response schema in `backend/app/schemas.py`, update `frontend/src/api/types.ts` in the same change.
4. **LLM features must degrade gracefully.** Without `ANTHROPIC_API_KEY`, check generation falls
   back to heuristics and explorer/RCA endpoints return 503 with a clear message. Preserve this.
5. **Adding a check type** touches exactly four places: `core/check_types.py` (registry entry +
   compiler), `schemas.py` (if params need validation), `frontend/src/api/types.ts` +
   `frontend/src/lib/checkMeta.ts` (label/description), and a test in `tests/test_checks.py`.
6. **Tests must pass before you commit**: `cd backend && pytest` and `cd frontend && npm run build`.
7. **Reference GitHub issues** in commits (`Refs #N` / `Closes #N`). Track new feature work as issues first (`gh issue create`).
8. **Privacy in prompts:** LLM prompts may include aggregates and ≤25 sample rows. Columns listed
   in a dataset's knowledge `pii_columns` are redacted before being sent. Keep it that way.

## Dev setup (Windows-first; POSIX equivalents in parentheses)

Prereqs: Python ≥3.12, Node ≥20, git. Optional: Docker, an Anthropic API key.

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

First login: `admin@example.com` / `admin123` (seeded in dev when no users exist; change via Settings).

### Standing demo (Docker) — keep it running

The owner reviews by clicking through a live UI. Keep the compose stack up as the standing demo:

```powershell
docker compose up --build -d        # UI on http://localhost:3000 (Postgres-backed, auto-restarts)
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
| Frontend typecheck | `cd frontend && npm run typecheck` |
| Frontend build | `cd frontend && npm run build` (runs typecheck first) |
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
- **Commits**: imperative subject, scope prefix (`backend:`, `frontend:`, `data:`, `ci:`), body
  explains why, reference issues.
- **API**: REST-ish under `/api/v1`. Everything except `/api/v1/health` and `/api/v1/auth/login`
  requires a Bearer JWT. Role gates: `viewer` (read), `editor` (mutate checks/triage), `admin` (connections/users).

## LLM integration notes

- Provider: Anthropic (`anthropic` SDK). Env: `ANTHROPIC_API_KEY`, `DQ_LLM_MODEL`
  (default in `app/config.py`), `DQ_LLM_MAX_EXPLORE_TURNS`.
- All agent SQL goes through the same `guard_sql()` + row-limit path as humans.
- The explorer and RCA agents are bounded loops (max turns, max rows per query) — keep bounds.
- When changing prompts, keep the JSON output contracts in `app/llm/prompts.py` in sync with the
  parsers next to them; parsers must tolerate markdown-fenced JSON.

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
| `regex check unsupported` on SQLite | expected: regex checks run via the Python fallback path (chunked fetch) |
| LLM endpoints return 503 | `ANTHROPIC_API_KEY` not set — heuristic generation still works |
| `npm run dev` proxy errors | backend not running on :8000, or `VITE_API_PROXY` overridden |
| `Cannot find module '@rollup/rollup-win32-x64-msvc'` | npm optional-deps bug (worse under OneDrive): `npm install @rollup/rollup-win32-x64-msvc --no-save` — do NOT add it to package.json (platform-specific; CI runs Linux) |
| Worker runs nothing | checks need `status=active` + a schedule; check `next_run_at` in DB |
| `duckdb.IOException: database is locked` | another process (e.g. a notebook) holds the DuckDB file — DuckDB allows one writer; connectors open read-only, so close the writer |
