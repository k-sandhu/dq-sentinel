# Contributing to DQ Sentinel

Thanks for your interest! This page covers everything a human contributor needs.
(If you are an AI coding agent, also read [AGENTS.md](AGENTS.md) — it adds
agent-specific rails on top of these rules.)

## TL;DR

1. Find or open a GitHub issue for what you want to change (label taxonomy:
   `backend`, `frontend`, `llm`, `ml`, `infra`, `data`, `bug`, `enhancement`).
2. Branch from `main`: `feat/<slug>` or `fix/<slug>`.
3. Make the change, keeping both test suites green (see below).
4. Open a PR that references the issue (`Closes #N`). CI must pass.

## Dev setup

Prereqs: Python ≥ 3.12, Node ≥ 20, git. Optional: Docker, an Anthropic (or any
OpenAI-compatible) API key for the LLM features — everything else works without one.

```bash
# one-shot bootstrap (venv, deps, sample data, .env):
./scripts/dev.sh            # Windows: .\scripts\dev.ps1

# then, in three terminals:
uvicorn app.main:app --reload --port 8000 --app-dir backend   # 1. API
cd backend && python -m app.worker                            # 2. scheduler worker
cd frontend && npm run dev                                    # 3. UI → http://localhost:5173
```

First login: `admin@example.com` / `admin123` (dev seed; the app refuses these
defaults when `DQ_ENV=prod`).

## Commands you'll use

| Task | Command |
|---|---|
| Backend tests | `cd backend && pytest -q` |
| Backend lint | `cd backend && ruff check app tests` |
| Frontend typecheck | `cd frontend && npm run typecheck` |
| Frontend unit tests | `cd frontend && npm run test` |
| Frontend build (typecheck + bundle) | `cd frontend && npm run build` |
| New DB migration | `cd backend && alembic revision --autogenerate -m "..."` (then review it) |
| Regenerate sample data | `python data/generate_sample_data.py --force` |
| End-to-end smoke vs a running API | `python scripts/e2e_smoke.py` |

CI runs ruff + pytest + tsc + the vite build on every push — run whatever you
touched locally first.

## Non-negotiable rules

These protect users; PRs that break them won't merge:

1. **Never write to a source database.** Every query against a user's data
   source goes through `backend/app/connectors/safety.py: guard_sql()` (single
   SELECT/CTE, denylist, forced LIMIT), and connectors open read-only where the
   driver supports it. This includes LLM-authored SQL.
2. **Never commit secrets or data files.** Config comes from env vars (see
   `.env.example`); `*.sqlite`, `*.duckdb`, and `.env` are gitignored — don't
   force-add them.
3. **Frontend and backend stay decoupled.** The only contract is the JSON API.
   If you change a response schema in `backend/app/schemas.py`, update
   `frontend/src/api/types.ts` in the same PR.
4. **LLM features must degrade gracefully.** Without an API key, check
   generation falls back to heuristics and agent endpoints return 503 with a
   clear message.
5. **App-DB schema changes ship with an Alembic migration** in the same PR
   (`tests/test_migrations.py` fails if migrations drift from `models.py`).
6. **PII stays redacted.** Columns listed in a dataset's knowledge
   `pii_columns` are redacted before anything is sent to an LLM.

## Common recipes

**Adding a check type** touches exactly four places:

1. `backend/app/core/check_types.py` — registry entry + violation-SQL compiler
2. `backend/app/schemas.py` — if the params need validation
3. `frontend/src/api/types.ts` + `frontend/src/lib/checkMeta.ts` — label/description
4. `backend/tests/test_checks.py` — a test proving it catches what it claims

**Changing an LLM prompt:** keep the JSON output contract in
`backend/app/llm/prompts.py` in sync with the parser next to it; parsers must
tolerate markdown-fenced JSON. Test loop changes with the `FakeProvider` in
`tests/test_llm_providers.py`.

## Conventions

- **Python**: 3.12+, type hints everywhere, Pydantic v2 idioms, SQLAlchemy 2.0
  style (`Mapped[]` / `mapped_column`). Lint with ruff (config in
  `backend/pyproject.toml`). Use module loggers, never `print()`.
- **TypeScript**: strict mode, function components + hooks, TanStack Query for
  all server state (no manual `useEffect` fetching), react-router v7. Plain CSS
  in `src/styles.css` — no UI-kit dependencies.
- **Commits**: imperative subject with a scope prefix (`backend:`, `frontend:`,
  `data:`, `ci:`, `docs:`); body explains *why*; reference issues
  (`Refs #N` / `Closes #N`).

## Reporting bugs / proposing features

Use the issue templates — they ask for the environment details (OS, deploy
mode, app DB, source engine) that make data-quality bugs reproducible. For
security vulnerabilities, **do not open a public issue** — see
[SECURITY.md](SECURITY.md).

## Windows / OneDrive note

If you develop on Windows inside a OneDrive-synced folder, see
[docs/dev-windows-notes.md](docs/dev-windows-notes.md) for known quirks
(file locks, venv placement, npm optional-deps). Short version: keep your
Python venv outside OneDrive.
