# DQ Sentinel Codebase Deep Dive and Implementation Action Plan

Original review: 2026-06-19 (`main`) — performance, structure, and known UI-flow gaps.
Expanded: 2026-06-23 — a seven-track production-readiness audit added **≈50 findings** across
security/authorization, concurrency-correctness, horizontal-scaling & resource lifecycle,
reliability & runtime ops, LLM/agent risk, accessibility, and CI/CD & supply chain.

This is a read-only review artifact and the **single tracking source** for hardening work. It does
not imply everything is fixed in one change; several items need staged migrations, benchmarking, or
product decisions first.

---

## 0. How to use this document

- **Tier 0 (production blockers)** are filed as GitHub issues — see the [Tier 0 section](#tier-0--production-blockers) and the Status column.
- **Everything else is tracked here.** This file is the backlog of record; promote items into issues/epics as they are scheduled. Suggested grouping: a **Security & Production-Readiness epic** (SEC+/CONC/REL/parts of OPS) and the existing perf-focused **Hardening** track (PERF/SCALE/BE/FE), plus the in-flight **analyst-workbench** (#66), **scorecards** (#116), **lineage** (#103) epics.
- **Status legend:** `→ #NNN` filed as an issue · `Tracked` in this doc only · `Done (#NNN)` already shipped · `Positive` a strength to preserve, not a defect.
- **Verification posture:** every finding cites `file:line` evidence verified against the code at review time. Line numbers drift — confirm before implementing. The [Corrections](#14-corrections--already-handled-do-not-re-litigate) section records areas that were checked and found **healthy** (don't spend effort there).

## Severity model

| Tier | Meaning | Typical action |
|---|---|---|
| **P0** | Can cause incorrect data, data loss, silent analyst-state loss, severe production instability, or an authentication/authorization bypass. | Fix before scale-up or production exposure. |
| **P1** | Likely to hurt security, scalability, maintainability, or operator trust as usage grows. | Plan into near-term engineering work. |
| **P2** | Important quality, ergonomics, or technical-debt item. | Fix opportunistically or when touching the area. |
| **P3** | Polish, cleanup, or future-proofing. | Batch with related work. |

## Executive summary

DQ Sentinel has a solid early architecture (FastAPI, SQLAlchemy, explicit read-only source connectors
behind `guard_sql()`, a worker process, TanStack Query, a broad backend test suite) and many
enterprise-grade concepts already built: profiling, generated checks, exception triage, lineage, RCA,
notifications, scorecards, dashboards, audit logs, and observability.

Two truths drive this plan:

1. **It grew feature-first.** Many features work but their implementation patterns are not ready for a
   high-volume deployment on limited hardware — repeated full-source scans, Python-side history
   bucketing, scheduler without durable backpressure, missing indexes, and large mixed-responsibility
   modules (Part 1, Part 7, Part 8).
2. **Production-readiness was never reviewed.** The original review was scoped to performance and code
   structure; it did not assess whether the system is **safe to operate**. The expansion found that
   the security, concurrency-correctness, horizontal-scaling, reliability, accessibility, LLM-risk, and
   supply-chain dimensions were largely unaddressed — including several **P0s reachable on a default
   deploy** (a publicly-known JWT signing secret with no guard; analyst triage state silently
   overwritten; runs silently skipped on every deploy; no app-DB query timeout; no object-level
   authorization).

The recommended direction is **incremental hardening, not a rewrite.** Sequencing rule: *fix
measurement, backpressure, and the Tier-0 blockers before increasing check volume or exposing the
system.* Without query budgets, queue-depth metrics, and the security/concurrency fixes below, adding
more checks or more frequent schedules will look functional in demos but be unstable — and unsafe —
under real load.

## System shape reviewed

DQ Sentinel is **not** a transaction-ingestion system; it monitors sources by querying them, stores
metadata in an app DB (SQLite dev / PostgreSQL prod), and presents results in a React UI. At
"millions of source rows/day" the stress points are: source scans/aggregations, scheduled-check
frequency and cost, retained `check_runs`/`exception_records`/events/audit/RCA/chat rows, dashboard
queries over retained history, worker concurrency, and browser bundle/render cost. It can support
high-volume sources **if** it runs bounded checks against well-indexed columns — it is not ready for
frequent broad scans across many wide tables on small hardware without the changes below.

---

## Master tracking index

Counts: **P0 ×9 · P1 ×35 · P2 ×34 · P3 ×13** (plus 2 positive findings). Tier-0 blockers are filed as #155–#159; the broader effort is umbrellaed by epic #160; everything else is tracked here.

### Tier 0 — production blockers (filed as issues)

| ID | Sev | Title | Status |
|---|---|---|---|
| T0-1 | P0 | Production config fail-fast — default `secret_key` + `admin123`, no guard (SEC+1/SEC+2/REL4) | → [#155](https://github.com/k-sandhu/dq-sentinel/issues/155) |
| T0-2 | P0 | Analyst triage state clobbered by auto-resolve; no optimistic concurrency (CONC1) | → [#156](https://github.com/k-sandhu/dq-sentinel/issues/156) |
| T0-3 | P0 | Worker has no graceful shutdown → runs silently skipped on deploy (CONC2/REL1) | → [#157](https://github.com/k-sandhu/dq-sentinel/issues/157) |
| T0-4 | P0 | App metadata DB has no statement/connect timeout (REL2) | → [#158](https://github.com/k-sandhu/dq-sentinel/issues/158) |
| T0-5 | P0/P1 | No object-level authorization (IDOR) — any editor queries any connection via `/query/run` (SEC+9/LLM3) | → [#159](https://github.com/k-sandhu/dq-sentinel/issues/159) |

### Part 1 — Performance, efficiency, scalability

| ID | Sev | Title | Status |
|---|---|---|---|
| PERF-1 | P0* | Profiling performs many full-table aggregations | Tracked |
| PERF-2 | P0* | Common check execution repeats scans | Tracked |
| PERF-3 | P1 | Custom SQL checks execute expensive SQL twice | Tracked |
| PERF-4 | P1 | ML/drift checks use bounded but heavy DataFrames | Tracked |
| PERF-5 | P0 | Scheduler has no durable backpressure | Tracked |
| PERF-6 | P1 | App-metadata indexes don't match high-volume queries | Tracked |
| PERF-7 | P1 | Dashboard/insights/scorecards load large history into Python | Tracked |
| PERF-8 | P1 | Offset pagination + per-row serialization will degrade | Tracked |
| PERF-9 | P1 | Exception auto-resolve loads/writes too much at once | Tracked |
| PERF-10 | P2 | Source result materialization uses `fetchall()` | Tracked |
| PERF-11 | P2 | Source introspection/health checks can become load generators | Tracked |
| PERF-12 | P2 | Compose deployment has no resource budget | Tracked |

### Part 2 — Scalability & resource lifecycle (new)

| ID | Sev | Title | Status |
|---|---|---|---|
| SCALE-1 | P1† | In-process LLM chat history + per-session locks break multi-replica | Tracked |
| SCALE-2 | P1† | WebSocket chat requires sticky sessions; undocumented/unenforced | Tracked |
| SCALE-3 | P1 | Source-engine cache unbounded (no LRU/idle/recycle); file-handle accrual | Tracked |
| SCALE-4 | P1 | `Connector(dsn)` test path leaks undisposed engines | Tracked |
| SCALE-5 | P1 | Index-adding migrations lock Postgres (no `CONCURRENTLY`) | Tracked |
| SCALE-6 | P1 | No retention/partitioning for high-growth tables (only audit) | Tracked |
| SCALE-7 | P2 | App-DB pool static / not env-tunable; connection-budget blowout | Tracked |
| SCALE-8 | P2 | Per-request source introspection, no cache | Tracked |
| SCALE-9 | P2 | No app-DB backup / PITR story | Tracked |
| SCALE-10 | P3 | Config/provider/engine singletons; no reload | Tracked |

### Part 3 — Concurrency & data integrity (new)

| ID | Sev | Title | Status |
|---|---|---|---|
| CONC-1 | P0 | Triage/auto-resolve last-writer-wins clobbers analyst state | → [#156](https://github.com/k-sandhu/dq-sentinel/issues/156) (T0-2) |
| CONC-2 | P0 | Worker has no graceful shutdown: in-flight checks abandoned + skipped runs | → [#157](https://github.com/k-sandhu/dq-sentinel/issues/157) (T0-3) |
| CONC-3 | P1 | SQLite FK enforcement off + no `ondelete` → integrity drift vs Postgres | Tracked |
| CONC-4 | P1 | Source-engine cache not invalidated on connection edit (no edit endpoint) | Tracked |
| CONC-5 | P2 | SQLite migration-on-startup race (Postgres path is safe) | Tracked |
| CONC-6 | P1 | Notifications/incidents: commit-then-dispatch double-fires; no idempotency | Tracked |
| CONC-7 | P1 | Manual+scheduled check double-run; lost `occurrence_count`; SQLite vs concurrency=4 | Tracked |
| CONC-8 | P2 | Datetime aware/naive seam between storage and freshness math | Tracked |
| CONC-9 | P2 | SLA `budget_consumed` lossy-capped at 2.0 | Tracked |
| CONC-10 | P3 | Scorecard/maintenance daily throttle is process-local, not cross-replica | Tracked |
| CONC-11 | P2 | Freshness check assumes worker clock == source-data clock (skew miscomputes staleness) | Tracked |

### Part 4 — Reliability & runtime operations (new)

| ID | Sev | Title | Status |
|---|---|---|---|
| REL-2 | P0 | App metadata DB has no statement/connect timeout | → [#158](https://github.com/k-sandhu/dq-sentinel/issues/158) (T0-4) |
| REL-3 | P1 | `/health` is a static stub; no readiness probe; no api/worker compose healthchecks | Tracked |
| REL-5 | P1 | Backend image runs as root, no HEALTHCHECK, single-stage, unpinned base | Tracked |
| REL-6 | P1 | No global exception handler; 500s lack request-id + consistent body | Tracked |
| REL-7 | P1 | No retry/circuit-breaker on source DB queries (dead source hammered) | Tracked |
| REL-8 | P1 | No retry/circuit-breaker on notification channels (lost alerts / dead-channel spam) | Tracked |
| REL-9 | P1 | Agent tool-call handlers run with no per-call timeout | Tracked |
| REL-10 | P2 | WebSocket has no app-level idle/read timeout | Tracked |
| REL-11 | P2 | No per-check wall-clock budget in worker (slow check starves pool) | Tracked |
| REL-12 | P2 | Auto-migrate-on-boot unsafe for multi-replica rolling deploys | Tracked |
| REL-13 | P3 | `.dockerignore` doesn't exclude `.git`/`.env` (latent leak) | Tracked |

### Part 5 — Security & authorization

| ID | Sev | Title | Status |
|---|---|---|---|
| SEC-1 | Positive | Source SQL safety guard is a strong foundation | Positive |
| SEC-2 | P1 | DSNs stored plaintext at rest | Tracked (#24) |
| SEC-3 | P2 | JWT in localStorage has tradeoffs | Tracked |
| SEC-4 | Positive | LLM privacy guardrails exist on the LLM path (preserve) — but see SEC+14 | Positive |
| SEC+1 | P0 | Default `secret_key` signs JWTs with no prod guard (admin forgery) | → [#155](https://github.com/k-sandhu/dq-sentinel/issues/155) (T0-1) |
| SEC+2 | P0 | Bootstrap admin `admin123` with no prod guard | → [#155](https://github.com/k-sandhu/dq-sentinel/issues/155) (T0-1) |
| SEC+3 | P1 | No login rate-limiting / brute-force protection / lockout | Tracked |
| SEC+4 | P1 | Stateless JWT — no revocation, logout, or refresh | Tracked |
| SEC+5 | P1 | SSRF via user-supplied notification & MCP URLs (and DSN hosts) | Tracked |
| SEC+6 | P1 | No security response headers (CSP/HSTS/X-Frame-Options/nosniff) | Tracked |
| SEC+7 | P2 | MCP `auth_token` stored plaintext (secrets-at-rest, beyond DSN) | Tracked |
| SEC+8 | P2 | Swagger `/docs` + OpenAPI exposed unauthenticated in prod | Tracked |
| SEC+9 | P0/P1 | No object-level authorization (IDOR); `/query/run` data-exfil | → [#159](https://github.com/k-sandhu/dq-sentinel/issues/159) (T0-5) |
| SEC+10 | P3 | CORS `allow_credentials` with wildcard methods/headers | Tracked |
| SEC+11 | P3 | Unbounded password → bcrypt 72-byte silent truncation | Tracked |
| SEC+13 | P2 | Chat WebSocket passes JWT as `?token=` query param (log leakage) | Tracked |
| SEC+14 | P1 | PII redaction is LLM-only — raw violating-row PII persisted, served & exported | Tracked |
| SEC+15 | P2 | No subject-level right-to-erasure for PII copied into exception rows | Tracked |
| SEC+16 | P2 | Encryption-in-transit not enforced (source DB / app DB / SMTP) | Tracked |
| OPS-18 | P2 | `/metrics` unauthenticated on the public API port | Tracked |

### Part 6 — LLM/agent risk & API robustness (new)

| ID | Sev | Title | Status |
|---|---|---|---|
| LLM-1 | P1 | No instruction/data separation → prompt injection → false RCA | Tracked |
| LLM-2 | P1 | No per-user/tenant rate limit or token-cost budget on agent endpoints | Tracked |
| LLM-3 | P1 | Chat tools exfil + bypass per-resource authz; `render_chart` 500 vs 200 cap | Tracked (authz → [#159](https://github.com/k-sandhu/dq-sentinel/issues/159)) |
| LLM-5 | P3 | Model id unpinned; adaptive-thinking gated by brittle version regex | Tracked |
| API-2 | P2 | `/api/v1` with no versioning/deprecation strategy | Tracked |
| API-3 | P2 | No request body-size limit; no `max_length` on SQL/free-text | Tracked |
| API-4 | P2 | Many list endpoints unbounded (no pagination/cap) | Tracked |
| API-5 | P2 | No idempotency keys on creating POSTs | Tracked |
| API-6 | P3 | No caching headers / ETags on read-heavy GETs | Tracked |

### Part 7 — Backend structure & maintainability

| ID | Sev | Title | Status |
|---|---|---|---|
| BE-1 | P1 | `schemas.py` is a cross-domain contract monolith | Tracked |
| BE-2 | P1 | `models.py` is a growing metadata-model monolith | Tracked |
| BE-3 | P1 | Core depends on API serialization (inverted dependency) | Tracked |
| BE-4 | P1 | API routers contain too much business logic | Tracked |
| BE-5 | P1 | `check_types.py` is a god module | Tracked |
| BE-6 | P3 | Router mounting is slightly irregular (lineage) | Tracked |
| BE-7 | P1 | Source connector layer needs capacity controls | Tracked |

### Part 8 — Frontend structure & maintainability

| ID | Sev | Title | Status |
|---|---|---|---|
| FE-1 | P1 | Routes are eagerly imported (no code-splitting) | Tracked |
| FE-2 | P1 | Large pages own too many responsibilities | Tracked |
| FE-3 | P1 | API types manually mirrored; duplicate `ExceptionPage` | Tracked |
| FE-4 | P2 | API calls not encapsulated by feature hooks | Tracked |
| FE-5 | P2 | Styling centralized; inline styles common | Tracked |
| FE-6 | P1 | Frontend lacks test coverage | Tracked |

### Part 9 — Frontend a11y, client-security & UX robustness (new)

| ID | Sev | Title | Status |
|---|---|---|---|
| FEX-1 | P1 | Navigation `<tr onClick>` rows are keyboard-inoperable | Tracked |
| FEX-2 | P1 | Tab strips have no tab semantics / arrow-key nav | Tracked |
| FEX-3 | P2 | Chat session switcher is `<div onClick>`; composer unlabeled | Tracked |
| FEX-4 | P2 | Streaming assistant output has no `aria-live` region | Tracked |
| FEX-5 | P2 | Secondary `useQuery` calls ignore `isError` (silent failures) | Tracked |
| FEX-7 | P3 | `DocsPage` parses timestamps without UTC normalization | Tracked |
| FEX-8 | P3 | `fmtDateTime` omits the year (cross-year ambiguity) | Tracked |
| FEX-9 | P3 | Global search dropdown lacks listbox/option semantics | Tracked |

### Part 10 — UI & product flows

| ID | Sev | Title | Status |
|---|---|---|---|
| UI-1 | P1 | Deep screens need breadcrumbs / context return | Done (#74) |
| UI-2 | P1 | Destructive actions need consistent confirm/undo | Done (#78) |
| UI-3 | P1 | Unsaved-work guards need to be consistent | Done (#76) |
| UI-4 | P1 | Exceptions filter state must stay transparent | Done (#75) |
| UI-5 | P2 | Disabled controls need recovery instructions | Tracked |

### Part 11 — Testing & verification

| ID | Sev | Title | Status |
|---|---|---|---|
| TEST-1 | P2 | Backend tests broad but SQLite-centered | Tracked |
| TEST-2 | P1 | Frontend has no unit/component tests | Tracked |
| TEST-3 | P1 | Performance tests are missing | Tracked |

### Part 12 — CI/CD, supply chain, observability coverage & ops docs (new)

| ID | Sev | Title | Status |
|---|---|---|---|
| OPS-1 | P1 | No software-composition / vulnerability scanning | Tracked |
| OPS-2 | P1 | Python dependencies unpinned, no lockfile | Tracked |
| OPS-3 | P1 | No secret scanning in CI | Tracked |
| OPS-4 | P2 | No SAST for the application's own code | Tracked |
| OPS-5 | P2 | Docker base images tag-pinned, not digest; no image scanning | Tracked |
| OPS-6 | P3 | No SBOM generation | Tracked |
| OPS-7 | P2 | No test-coverage measurement or gate | Tracked |
| OPS-8 | P2 | Frontend has no lint/test tooling (or a11y checks) in CI | Tracked |
| OPS-9 | P2 | CI never builds/scans/smoke-tests images; e2e smoke not in CI | Tracked |
| OPS-10 | P3 | GitHub Actions pinned to floating tags, not SHAs | Tracked |
| OPS-11 | P2 | No branch-protection / required-status enforcement | Tracked |
| OPS-12 | P1 | No scheduler backlog observability (queue depth / oldest-due) | Tracked |
| OPS-13 | P2 | No per-user/feature LLM cost attribution | Tracked |
| OPS-14 | P3 | Grafana dashboard missing notification/latency/incident panels | Tracked |
| OPS-15 | P2 | No backend-schema ↔ frontend-types contract test | Tracked |
| OPS-16 | P2 | No migration downgrade/rollback test | Tracked |
| OPS-17 | P1 | No SECURITY.md / threat model / runbook / DR / CONTRIBUTING | Tracked |

\* PERF-1/PERF-2 are P0 specifically for large/wide datasets and frequent checks. † SCALE-1/SCALE-2 are P1 today and **P0 the moment the API is scaled past one replica**.

---

## Tier 0 — production blockers

These five are filed as GitHub issues; they are cheap-to-moderate fixes with severe blast radius and
should land before any production exposure or scale-up.

### T0-1 — Production config fail-fast (default secret_key + admin password) · P0 · [#155](https://github.com/k-sandhu/dq-sentinel/issues/155) · `security` `backend`
Combines **SEC+1**, **SEC+2**, **REL-4**.
- **Evidence:** `backend/app/config.py:30` `secret_key="dev-only-secret-change-me"`; `backend/app/security.py:39` signs JWTs with it; `backend/app/config.py:32-33` + `backend/app/db.py:104-113` seed `admin@example.com`/`admin123`; `docker-compose.yml:28` ships an insecure fallback; `backend/app/config.py` has **zero** validators.
- **Problem:** A deploy that forgets `DQ_SECRET_KEY` signs/verifies all JWTs with a secret that is in the public repo → anyone can forge an admin token (full auth bypass). Default admin creds compound it. No startup guard.
- **Fix:** Introduce `DQ_ENV` (dev|prod). A Pydantic `model_validator(mode="after")` raises on boot when `env=prod` and `secret_key` is a known default / `< 32` chars, or `bootstrap_admin_password == "admin123"`. Optionally force first-login password rotation.
- **Validate:** Unit test that prod + default secret/password raises; a strong secret boots; forging a token with the default secret authenticates on a dev box (demonstrates risk) but prod refuses to start.

### T0-2 — Analyst triage state clobbered by auto-resolve; no optimistic concurrency · P0 · [#156](https://github.com/k-sandhu/dq-sentinel/issues/156) · `backend` `bug`
**CONC-1.** Violates the standing "analyst state is sacred" cross-cutting standard (#66).
- **Evidence:** `backend/app/core/runner.py:141-181` `_auto_resolve_passing` runs a set-based `UPDATE … WHERE status='open'` overwriting `note`→"auto-resolved", `marked_by_id`→None, `marked_at`→now; `backend/app/api/exceptions_api.py:302-360` triage is blind read-modify-write; no version column on `ExceptionRecord` (`models.py:358-410`).
- **Problem:** A passing scheduled run, or two analysts triaging the same batch, silently overwrites human-authored notes/assignment/attribution. The append-only `ExceptionEvent` trail survives, but the record the queues read is corrupted (last-writer-wins, undetected).
- **Fix:** Add a `version` column + `__mapper_args__ version_id_col`, surface as `ETag`/`If-Match` on triage/comment endpoints → 409 on mismatch. At minimum, stop the machine path overwriting human fields (use a dedicated `resolved_reason`, never `note`/`marked_by_id`).
- **Validate:** Concurrency test interleaving an analyst note-write with a passing-run auto-resolve; assert the human fields survive (or a 409 is raised).

### T0-3 — Worker has no graceful shutdown → runs silently skipped on deploy · P0 · [#157](https://github.com/k-sandhu/dq-sentinel/issues/157) · `backend` `infra`
**CONC-2 / REL-1.**
- **Evidence:** `backend/app/worker.py:14-19` + `backend/app/core/scheduler.py:157-175` is `while True` with **no SIGTERM/SIGINT handler**; `scheduler.py:142-153` advances `next_run_at` **before** `executor.submit(_execute)`. No `signal`/`atexit`/shutdown anywhere in `backend/`.
- **Problem:** On `docker stop`/rollout/scale-down the process dies; in-flight checks are abandoned mid-transaction, and any check whose slot was already advanced is **silently skipped until its next schedule** — no run row, no error, a per-deploy blind spot (up to a full interval for hourly/daily checks).
- **Fix:** Install SIGTERM/SIGINT handlers that set a stop flag and `executor.shutdown(wait=True)` within a grace window; advance `next_run_at` only **after** a successful run, or record a `claimed_until` lease so a crashed run is re-claimed rather than skipped. (Dovetails with the PERF-5 durable-queue work but is a distinct correctness fix.)
- **Validate:** SIGTERM mid-run → the in-flight check still produces a terminal run row (or re-runs); a unit test asserts the stop flag breaks the loop.

### T0-4 — App metadata DB has no statement/connect timeout · P0 · [#158](https://github.com/k-sandhu/dq-sentinel/issues/158) · `backend` `infra`
**REL-2.**
- **Evidence:** `backend/app/db.py:34` creates the Postgres engine with `pool_pre_ping=True` but **no** `statement_timeout`, `connect_timeout`, or `pool_timeout`. Contrast the *source* dialect, which sets `statement_timeout=30000` (`backend/app/connectors/dialects.py:52`).
- **Problem:** A slow/locked/failing-over app DB makes every API worker thread and the worker's poll loop block indefinitely — the whole system freezes with no error, no metric, no recovery. `pool_pre_ping` validates liveness, not query duration.
- **Fix:** `connect_args={"connect_timeout":5, "options":"-c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000"}` on the Postgres branch + a bounded `pool_timeout`; make values configurable (`DQ_DB_STATEMENT_TIMEOUT_MS`). Leave SQLite's `busy_timeout` as is.
- **Validate:** Integration test against Postgres that `SELECT pg_sleep(...)` aborts at the configured timeout instead of hanging.

### T0-5 — No object-level authorization (IDOR); `/query/run` data-exfil · P0 (multi-tenant) / P1 · [#159](https://github.com/k-sandhu/dq-sentinel/issues/159) · `security` `backend`
**SEC+9 / LLM-3 (authz).** Partially tracked by #26 (RBAC) and #72 (tenancy completion) — but the `/query/run` edge warrants near-term attention regardless.
- **Evidence:** `backend/app/security.py:58-66` `require_role` is role-rank only (ignores ownership); `backend/app/api/query.py:24-62` lets any **editor** run arbitrary guarded SQL against **any** `connection_id`; viewers list every connection's datasets/exceptions/runs. In-code admissions: `backend/app/api/search.py:10-12` and `backend/app/api/insights.py:60-61` TODOs. Only `ChatSession` and `CustomDashboard` enforce ownership.
- **Problem:** In any multi-team/multi-tenant deployment this is a horizontal-privilege / data-segregation failure: a viewer sees another team's inventory; an editor exfiltrates any source's data via `POST /query/run`. Chat tool calls (`chat_agent.py:194`) inherit the same gap, and `render_chart` returns up to 500 rows vs `run_sql`'s 200 (`adhoc.py:16` vs `config.py:85`).
- **Fix:** Introduce a per-connection grant model (or team-scoped connections) and a reusable `assert_can_access(connection_id|dataset_id)` dependency threaded through every by-id and list endpoint (404 on unauthorized to avoid existence leaks). Prioritize `/query/run` and the dataset/exception/run readers; enforce the same authz inside chat tool handlers and unify the chart/SQL row caps. Coordinate with #26/#72 (likely the home for the full model).
- **Validate:** Two users with disjoint grants — assert A gets 404/empty on B's data and 403/404 on `POST /query/run` for B's connection; admins still see all.

---

## 1. Performance, efficiency, and scalability

### PERF-1 — Profiling performs many full-table aggregations · P0 (large/wide) · Tracked
- **Evidence:** `backend/app/core/profiler.py:82,90,93,106-107,126-127,165`.
- **Problem:** `profile_dataset()` does `COUNT(*)`, fetches `SELECT *` up to 50k rows into pandas, then per-column exact `COUNT`, `COUNT(DISTINCT)`, `MIN/MAX`, and top-value `GROUP BY`. Work scales with rows × columns; a 100-column table triggers hundreds of source queries; `SELECT *` pulls wide rows; the app caps returned rows but not source work.
- **Fix:** Split profiling into basic/extended/expensive tiers; prefer approximate distinct (engine-native); avoid `SELECT *` (project only sampled columns); add a per-dataset budget (max queries / seconds / columns / memory); cache+reuse row count; store profile freshness/quality (sampled vs exact vs partial vs stale).
- **Validate:** Query-count assertions for a synthetic wide table; benchmarks at 10/50/100/300 columns; metrics for profile source-query count, elapsed, sampled bytes.

### PERF-2 — Common check execution repeats scans · P0 (frequent/large) · Tracked
- **Evidence:** `backend/app/core/check_types.py:77,80,83,87,153,159,167,174`.
- **Problem:** `_sample_where()` runs violation-count + sample-rows + total-count (2–3 source queries per check); unique checks build duplicate groups/counts/samples; total row count is recomputed though it changes slowly. Cost grows with number of checks, not just datasets.
- **Fix:** Treat row count as a cached dataset metric (TTL or a dedicated row-count check); return "at least N violations" + capped sample when exact is too expensive; add a check-execution-plan layer so checks on one table share precomputed facts and combine compatible aggregates (e.g. multi-column null counts in one query); add per-check cost hints + a "large table mode".
- **Validate:** Result-semantics tests with cached/stale row count; source-query-count assertions for many checks on one table; a benchmark of 10 checks on 1M rows.

### PERF-3 — Custom SQL checks execute expensive SQL twice · P1 · Tracked
- **Evidence:** `backend/app/core/check_types.py:648,651,654`.
- **Problem:** Custom SQL is wrapped as `SELECT COUNT(*) FROM (...)` then, if non-zero, the original runs again for a sample — expensive joins/CTEs run twice; the outer LIMIT doesn't reduce count cost.
- **Fix:** Add `count_mode` (`exact` / `sample_only` / `exists_plus_sample`); default to sample-first + `EXISTS` probe on limited hardware; store `violation_count_exact=false`; label approximate counts in the UI.
- **Validate:** Unit-test the modes; integration test that sample-only mode runs the SQL once.

### PERF-4 — ML/drift checks use bounded but heavy DataFrames · P1 (P0 small-hw concurrent) · Tracked
- **Evidence:** `backend/app/config.py:83,86`; `backend/app/core/check_types.py:665,671,924,931`; `backend/app/core/ml.py:52-53`.
- **Problem:** `ml_max_rows=50_000`, ML fetches `SELECT *`, IsolationForest uses `n_estimators=200, n_jobs=-1`; with `worker_concurrency=4`, four concurrent ML checks hold multiple 50k DataFrames and oversubscribe CPU.
- **Fix:** Separate settings (`DQ_ML_MAX_ROWS`, `DQ_ML_N_JOBS`, `DQ_ML_MAX_CONCURRENT_CHECKS`, `DQ_PROFILE_MAX_ROWS`); default `n_jobs=1`; project numeric columns instead of `SELECT *`; reservoir/source-side sampling; make ML opt-in for large tables.
- **Validate:** Test ML passes projected columns; a concurrent-ML stress test with memory monitoring.

### PERF-5 — Scheduler has no durable backpressure · P0 (prod) · Tracked
- **Evidence:** `backend/app/core/scheduler.py:104,135,139,143,149,153,165`.
- **Problem:** Each poll does inline maintenance, claims up to 20 due checks, advances `next_run_at` before execution, submits to a process-local `ThreadPoolExecutor`. No durable job table, queue depth, lease timeout, per-source limit, or backlog policy → queued futures grow, a dying worker skips work, one source can be overloaded, maintenance competes with scheduling.
- **Fix:** A `check_jobs` table (`check_id, scheduled_for, status, claimed_by, claimed_until, attempt_count, last_error`); row-level-lock claiming on Postgres; stale-lease recovery; per-connection/per-dataset concurrency limits; backlog policy (skip stale, coalesce missed, always run latest); move maintenance to its own jobs. (Note: the **graceful-shutdown / skipped-run** correctness facet is split out as T0-3.)
- **Validate:** Crash/retry tests; two workers don't run the same job; per-source limit; metrics for queue depth, oldest-due age, in-flight, skipped/coalesced.

### PERF-6 — App-metadata indexes don't match high-volume queries · P1 · Tracked
- **Evidence:** `backend/app/models.py:245,270,358`; `backend/migrations/versions/0001_baseline.py`.
- **Problem:** Missing/weak composite indexes for `CheckRun.started_at>=cutoff GROUP BY status`, dataset+started_at, latest-run-per-check, `ExceptionRecord.status+first_seen_at`, `dataset_id+first_seen_at`, `check_id+status`, `status+marked_at`, `last_run_id`, and exception text search.
- **Fix:** Alembic migration with composite indexes driven by `EXPLAIN ANALYZE`; partial indexes for `status='open'` on Postgres; trigram/full-text for exception search. **Build large-table indexes with `CONCURRENTLY`** (see SCALE-5).
- **Validate:** Before/after `EXPLAIN ANALYZE` for dashboard/exceptions/runs/insights/scorecards; migration-drift tests.

### PERF-7 — Dashboard/insights/scorecards load large history into Python · P1 · Tracked
- **Evidence:** `backend/app/api/dashboard.py:47-48,52`; `backend/app/api/insights.py:96,168`; `backend/app/core/scorecards.py:361,374,378,649,653`.
- **Problem:** Trend/matrix/series load all rows in a window and bucket in Python; scorecards load all historical runs to derive latest status; backfill repeatedly captures snapshots. Result size grows with retained history; CPU/memory shift from DB to API.
- **Fix:** Push bucketing to SQL (`GROUP BY date_trunc(...)`, SQLite-compatible variant for tests); window functions / `Check.last_status` for latest-per-check; persist daily rollups for long windows; batch backfill with aggregates. Compounds with SCALE-6 (unbounded history).
- **Validate:** Response-shape parity tests; load tests with 1M `check_runs` + 1M `exception_records`.

### PERF-8 — Offset pagination + per-row serialization will degrade · P1 · Tracked
- **Evidence:** `backend/app/api/runs.py:29,60-61`; `backend/app/api/serialize.py:32,39,54,56,62`; `backend/app/api/exceptions_api.py:135,145,147`.
- **Problem:** Runs/exceptions use `count()` + offset; `run_out()` does a per-run exception count; `exception_out()` does per-row check/dataset/user lookups (N+1). Offset slows at high pages; exact count dominates large filtered sets.
- **Fix:** Cursor pagination for high-volume lists; `has_more` instead of exact totals where non-essential; bulk-load related rows; serializer functions accepting preloaded maps. (Distinct from API-4, which is about *unbounded* lists.)
- **Validate:** Query-count tests for the list pages; cursor stability under new inserts.

### PERF-9 — Exception auto-resolve loads/writes too much at once · P1 · Tracked
- **Evidence:** `backend/app/core/runner.py:141,151,155,160,171`.
- **Problem:** A passing check loads all open exception IDs into Python, bulk-updates, then inserts one `ExceptionEvent` per exception → memory spike + huge transaction + write amplification on a check with many open exceptions. (See also T0-2 for the *correctness* facet of this same code.)
- **Fix:** Batch the auto-resolve; summarized system event for very large batches; optional background event materialization; hard cap with "partially completed" surfacing.
- **Validate:** Auto-resolve 100k open exceptions in batches; confirm audit/event semantics acceptable for compliance.

### PERF-10 — Source result materialization uses `fetchall()` · P2 (P1 wide/concurrent) · Tracked
- **Evidence:** `backend/app/connectors/sa.py:201,213,230,236`.
- **Problem:** `run_select()` uses `fetchall()`, `fetch_df()` uses `read_sql` — capped results are still all-at-once and can be wide; concurrent requests multiply memory; no streaming for exports.
- **Fix:** Streaming/chunked fetch helpers; project fewer columns for sample/ML paths; track row width / payload size; response-size limits (not just row count).
- **Validate:** Payload-cap tests; memory profiling for wide-row results.

### PERF-11 — Source introspection/health checks can become load generators · P2 · Tracked
- **Evidence:** `backend/app/connectors/sa.py:116,138`; `backend/app/api/connections.py:19,40`.
- **Problem:** `list_tables()` enumerates all schemas/tables/views; `schema_tree()` calls `get_columns()` per object; fleet health probes all connections (up to 8 concurrent). Large warehouses → many metadata queries; health endpoints hit every source.
- **Fix:** Cache schema trees per connection (TTL + explicit refresh); paginate/search browse endpoints; max-object cap with "narrow your search"; async/cached fleet health; per-source probe timeout. (See SCALE-8 for the interactive read paths.)
- **Validate:** Browse with thousands of synthetic objects; metrics for introspection time/object counts.

### PERF-12 — Compose deployment has no resource budget · P2 · Tracked
- **Evidence:** `docker-compose.yml:7,23,48,59,67`.
- **Problem:** Postgres/API/worker/frontend/Prometheus/Loki/Promtail/Grafana have restart+healthcheck but no CPU/memory reservations or limits and no small-hardware profile → observability competes with the app; ML/worker can overrun.
- **Fix:** A "small hardware" profile (worker concurrency 1–2, ML constrained, lower row/pool caps, optional observability); resource guidance docs; override files (`docker-compose.small.yml` / `.observability.yml` / `.prod.yml`).
- **Validate:** Smoke test on a constrained VM; track memory/CPU under sample workload.

---

## 2. Scalability & resource lifecycle (new)

### SCALE-1 — In-process LLM chat history + per-session locks break multi-replica · P1 (P0 on scale) · Tracked
- **Evidence:** `backend/app/llm/chat_agent.py:142-163` (`_histories` OrderedDict, max 32, process-local), `:368-394` (`_turn_locks` process-local); `backend/app/api/chat.py:95`.
- **Problem:** Provider-native history (thinking signatures, tool-call ids) lives only in the process that handled the turn; the per-session "one answer in progress" lock is process-local. Behind >1 API replica, a follow-up turn on another replica silently falls back to text-only rehydration (breaking Anthropic thinking continuation) and two replicas can run concurrent turns → interleaved `chat_messages` writes + duplicated spend.
- **Fix:** Move conversation state to a shared store (persist full provider-native turns incl. `raw`, or Redis); replace `_turn_lock` with a DB/Redis advisory lock. Until then, document chat as single-replica + sticky.
- **Validate:** Two replicas round-robin; start a tool-using chat on A, force the next turn to B; assert continuity + no duplicate assistant rows.

### SCALE-2 — WebSocket chat requires sticky sessions; undocumented/unenforced · P1 (P0 on scale) · Tracked
- **Evidence:** `frontend/nginx.conf:21-30` (Upgrade headers but no `upstream`/`ip_hash`); `docker-compose.yml:23-46` (single `api`, no replica/affinity note); WS state bound to one process (`chat.py:114-227`).
- **Problem:** Scaling `api` round-robins reconnects/REST calls across replicas; combined with SCALE-1 this is a correctness issue, not just performance.
- **Fix:** nginx `upstream` with `ip_hash` (or cookie/ingress affinity) + document the requirement; properly, fix SCALE-1 so affinity is a nicety.
- **Validate:** Scale `api` to 2, hammer reconnect; confirm a session's turns land on one replica (or, post-SCALE-1, correctness regardless).

### SCALE-3 — Source-engine cache unbounded (no LRU/idle/recycle); file-handle accrual · P1 · Tracked
- **Evidence:** `backend/app/connectors/sa.py:34-35` (`_engines` dict), `:96-102` (never size-checked), `:78-83` (only delete evicts); `backend/app/connectors/dialects.py:48-53,64-69` (`pool_size=5, max_overflow=5`).
- **Problem:** One Engine per connection for process life; worst case `N×10×(workers+1)` source connections, plus a leaked OS file handle per cached DuckDB/SQLite file engine. No `pool_recycle` → stale connections linger.
- **Fix:** Bounded LRU (dispose evicted); `pool_recycle`/`pool_timeout`; `NullPool` (or pool_size=1) for file dialects; optional idle reaper.
- **Validate:** Register 50 connections, exercise once; assert cache ≤ cap, disposed pools closed, file-handle count plateaus.

### SCALE-4 — `Connector(dsn)` test path leaks undisposed engines · P1 · Tracked
- **Evidence:** `backend/app/connectors/sa.py:103-104` (uncached engine when `connection_id is None`); `backend/app/api/connections.py:80` (`POST /connections/test` builds `Connector(body.dsn)`, never disposes).
- **Problem:** Every pre-save DSN test leaks a pool/file handle proportional to admin activity; GC doesn't reliably close SQLAlchemy pools.
- **Fix:** try/finally `engine.dispose()` (or a `Connector` context manager/`close()`); `NullPool` for the transient path.
- **Validate:** 200× `POST /connections/test`; assert server connection + fd counts return to baseline.

### SCALE-5 — Index-adding migrations lock Postgres (no `CONCURRENTLY`) · P1 · Tracked
- **Evidence:** `backend/migrations/env.py:30-31,57` (`render_as_batch` only for SQLite); existing index migrations use plain `create_index` (`0007_incidents.py:50-56`); `backend/app/db.py:83-91` holds an advisory lock across `upgrade head`; Alembic wraps migrations in a transaction.
- **Problem:** The PERF-6 indexes on a multi-million-row `exception_records` would take a write-blocking lock for the build duration, gated behind the startup advisory lock → minutes of write-downtime + delayed boot.
- **Fix:** Author large-table index migrations with `op.create_index(..., postgresql_concurrently=True)` inside `op.get_context().autocommit_block()`; keep batch mode for SQLite; document in AGENTS.md rule #9.
- **Validate:** On a seeded large table, run the migration under a concurrent writer; assert writers aren't blocked and the index is `valid`.

### SCALE-6 — No retention/partitioning for high-growth tables (only audit) · P1 · Tracked
- **Evidence:** Only `scheduler.py:67-88` `purge_audit_log` (driven by `config.py:124`); `models.py:392` TODO on `ExceptionRecord`; unbounded: `exception_records`, `exception_events`, `check_runs`, `incident_events`, `sla_evaluations`, `chat_messages`, `rca_sessions`, snapshots.
- **Problem:** Everything except audit grows forever — these are the PERF-7 query targets; `exception_records` UPDATE-in-place reconciliation bloats heap+indexes without tuned autovacuum; `sla_evaluations` adds ~288 rows/SLA/day.
- **Fix:** Per-table retention settings + throttled purge passes; Postgres time-**partitioning** of the churn tables (retention = `DROP PARTITION`, plus partition pruning for PERF-7); tune autovacuum on `exception_records`; archive resolved/muted exceptions to a cold table before purge.
- **Validate:** Seed 5M rows over months; verify purge/partition-drop reclaims space, dead-tuple ratio stays bounded, and PERF-7 queries stay flat.

### SCALE-7 — App-DB pool static / not env-tunable; connection-budget blowout · P2 · Tracked
- **Evidence:** `backend/app/db.py:34` (`pool_size=10, max_overflow=20`, no `pool_recycle`/`pool_timeout`, not configurable); worker runs concurrency 4 + poll on the same pool.
- **Problem:** `pool × uvicorn-workers × replicas + worker` can exceed Postgres `max_connections` (compose default ~100) → `too many clients`. No `pool_recycle` for proxy-severed connections.
- **Fix:** `DQ_DB_*` settings (`pool_size`/`max_overflow`/`pool_recycle`/`pool_timeout`); document the budget formula; consider PgBouncer for high fan-out.
- **Validate:** Boot API `--workers 4` + worker against `max_connections=100`; assert steady-state within budget and survives a forced server-side connection kill.

### SCALE-8 — Per-request source introspection, no cache · P2 · Tracked
- **Evidence:** `backend/app/connectors/sa.py:138-147` (`schema_tree` → `get_columns` per table, no memoization); hot callers `query.py:73,87,208`, `connections.py:135`, `lineage.py:34,462`, `rca_agent.py:117`.
- **Problem:** Each workbench open / lineage build / RCA hits the source catalog fresh (O(tables) round-trips), amplifying source load + pool pressure; multiple analysts multiply it. (Interactive-read counterpart to PERF-11.)
- **Fix:** Short-TTL cache (in-memory or Redis for replica-safety) keyed by connection, invalidated on dataset register/delete; or serve the sidebar from the existing `SchemaSnapshot` table (`models.py:121`).
- **Validate:** Open the schema sidebar twice within the TTL against an instrumented source; assert ~0 catalog round-trips on the second call.

### SCALE-9 — No app-DB backup / PITR story · P2 · Tracked
- **Evidence:** Repo-wide grep for `backup|pg_dump|restore|PITR|wal-g|pgbackrest` → none; `docker-compose.yml:8-21` single `pgdata` volume, no backup sidecar/WAL archiving; no backup doc.
- **Problem:** The app DB is the system of record (checks, triage history, incidents, audit, contracts, SLAs) with no automated backup or PITR — a volume loss or bad migration is unrecoverable.
- **Fix:** Backup sidecar / scheduled `pg_dump` or continuous WAL archiving (pgBackRest/wal-g) to a separate store; document RPO/RTO + a tested restore runbook (see OPS-17).
- **Validate:** A backup→drop→restore drill; restored DB at `alembic current == head`, row counts match.

### SCALE-10 — Config/provider/engine singletons; no reload · P3 · Tracked
- **Evidence:** `backend/app/config.py:152-154` (`lru_cache get_settings`); `backend/app/llm/providers.py:344-357` (`lru_cache _build_provider`); `backend/app/db.py:14-36` (module-global engine).
- **Problem:** Settings, LLM provider clients, and the DB engine are process-lifetime singletons — rotating keys / switching providers / re-tuning pools requires a full restart of every replica + the worker.
- **Fix:** Admin/signal-triggered cache invalidation (`cache_clear()` + engine rebuild) for the reloadable subset, or explicitly document "config changes require restart."
- **Validate:** Flip `DQ_LLM_MODEL` + reload; `provider_info()` reflects it without restart (or the doc says restart-only).

---

## 3. Concurrency & data integrity (new)

> CONC-1 and CONC-2 are Tier-0 (T0-2, T0-3) — full write-ups above.

### CONC-3 — SQLite FK enforcement off + no `ondelete` → integrity drift vs Postgres · P1 · Tracked
- **Evidence:** `backend/app/db.py:25-32` sets `WAL` + `busy_timeout` but **not** `PRAGMA foreign_keys=ON`; no FK in `models.py` declares `ondelete=`.
- **Problem:** On SQLite (dev/test) FKs are unenforced — orphaned rows commit clean; on Postgres (prod) FKs are enforced but default to `RESTRICT`, so future raw-SQL retention/purge deletes (#30, SCALE-6) will fail on Postgres while passing on SQLite. Tests are green locally, behavior differs in prod.
- **Fix:** Add `PRAGMA foreign_keys=ON` to the SQLite connect hook; add explicit `ondelete` to every FK (`CASCADE` for owned children, `SET NULL` for soft refs like `last_run_id`/`assigned_to_id`/`marked_by_id`) via a migration.
- **Validate:** Pragma on → run the suite (tests relying on dangling FKs now fail, revealing the gap); migration-drift test asserting `ondelete` rules.

### CONC-4 — Source-engine cache not invalidated on connection edit (no edit endpoint) · P1 · Tracked
- **Evidence:** `backend/app/connectors/sa.py:34-35,91-104` (cache by id, only `dispose_connection` on delete); `backend/app/api/connections.py` exposes only POST/DELETE/test — **no edit endpoint** (a typo'd DSN requires delete+recreate, cascading away all datasets/checks).
- **Problem:** If a DSN/credentials change (rotation, future edit feature), the API and worker keep stale cached engines until restart and can disagree.
- **Fix:** Add a connection-edit endpoint that disposes the cached engine after commit; since the worker is a separate process, key the cache on a DSN fingerprint / `updated_at` or use a cross-process invalidation signal.
- **Validate:** Edit a DSN → next `connector_for` builds a fresh engine; a stale cached engine isn't reused after `dispose_connection`.

### CONC-5 — SQLite migration-on-startup race (Postgres path is safe) · P2 · Tracked
- **Evidence:** `backend/app/db.py:83-91` (Postgres advisory lock — good); the SQLite branch has no equivalent, so api + worker both `command.upgrade` concurrently, relying on `busy_timeout=5000`.
- **Problem:** A slow SQLite migration can exceed `busy_timeout` during the api/worker boot race → `database is locked`. (Prod is Postgres, so this is dev-only — but it means migration races are only ever seen in dev.)
- **Fix:** Serialize the SQLite path (file lock / retry loop) or have the worker wait for the API to migrate.
- **Validate:** Launch api+worker against a fresh SQLite DB with a slow migration; assert no lock error.

### CONC-6 — Notifications/incidents: commit-then-dispatch double-fires; no idempotency · P1 · Tracked
- **Evidence:** `backend/app/core/incidents.py:83-88,279-306,191-211` (commit, then dispatch, then a second commit sets `last_notified_at`); `backend/app/core/notify.py:108-114` etc. — no idempotency key except PagerDuty (`dedup_key`) and Jira/ServiceNow (`external_refs`).
- **Problem:** A crash between the two commits leaves "should notify but `last_notified_at` is null" → re-decides + re-sends; Slack/Teams/email/webhook carry no dedupe token → duplicate pages. Escalation has the same split.
- **Fix:** Set `last_notified_at`/`next_escalation_at` in the same transaction as the "notified" event; pass a stable idempotency key (or persist a `(incident_id, action, occurrence_count)` sent-marker) for channels without native dedupe.
- **Validate:** Simulate a crash between commits; assert no duplicate send on restart.

### CONC-7 — Manual+scheduled double-run; lost `occurrence_count`; SQLite vs concurrency=4 · P1 · Tracked
- **Evidence:** `backend/app/config.py:90` (`worker_concurrency=4`) + `scheduler.py:165` vs the "single worker against SQLite" docstring; `runner.py:113` (`occurrence_count = (count or 0)+1`, read-modify-write); `checks.py:145-156` (`POST /checks/{id}/run` bypasses the scheduler claim).
- **Problem:** A manual run racing a scheduled run double-executes + double-reconciles; overlapping runs lose an `occurrence_count` increment; the default 4 SQLite writers can hit `database is locked` despite the docstring.
- **Fix:** Apply the scheduler claim (or a row lock) to the manual-run endpoint; make the increment atomic (`SET occurrence_count = occurrence_count + 1`); default `worker_concurrency=1` when the app DB is SQLite.
- **Validate:** Fire manual+scheduled concurrently → one run (or correct count); stress 4 SQLite writers → no lock errors.

### CONC-8 — Datetime aware/naive seam · P2 · Tracked
- **Evidence:** `backend/app/models.py:25-26` stores **naive** UTC; `backend/app/core/check_types.py:422-426,509,518` computes freshness in **aware** UTC and persists `metrics["latest"]` as an aware ISO string with `+00:00`.
- **Problem:** Storage is uniformly naive (good — "honest UTC" holds for storage), but the freshness `latest` is aware; comparing it to a naive `utcnow()` raises `TypeError`. A tripwire for the next person, not a live bug.
- **Fix:** Normalize at the boundary — have `_parse_timestamp` return naive-UTC so freshness math matches storage (or standardize on aware everywhere); add asserts/comments at the seam.
- **Validate:** `metrics["latest"]` round-trips comparable to `utcnow()` without a tz error.

### CONC-9 — SLA `budget_consumed` lossy-capped at 2.0 · P2 · Tracked
- **Evidence:** `backend/app/core/sla.py:85-94` (`budget = min(2.0, (bad/total)/allowed)`; div-by-zero correctly guarded).
- **Problem:** Once the error budget is exceeded, `budget_consumed` saturates at 2.0 — a 2×-over and a 5×-over breach look identical, misleading the reliability signal.
- **Fix:** Report the true ratio (or a larger documented sentinel + `budget_exhausted: bool`); clamp only in the presentation layer.
- **Validate:** Distinguishable `budget_consumed` at 1×/2×/5× the budget.

### CONC-10 — Scorecard/maintenance daily throttle is process-local, not cross-replica · P3 · Tracked
- **Evidence:** `backend/app/core/scheduler.py:26-28,50-64,67-88` (module-global `_last_scorecard_snapshot`/`_last_audit_purge`/`_last_sla_eval`).
- **Problem:** With multiple workers each replica runs the daily snapshot/purge/SLA pass; DB upsert prevents duplicate snapshot rows but it's N× work and SLA breach evaluation could double-notify; restart resets the global → immediate re-run.
- **Fix:** Persist last-run timestamps (a `system_job_runs` row / advisory lock per job) so the throttle is DB-backed (dovetails with PERF-5's "maintenance as its own jobs").
- **Validate:** Two workers → daily passes run once total; SLA breach notifies once.

### CONC-11 — Freshness check assumes worker clock == source-data clock · P2 · Tracked
- **Evidence:** `backend/app/core/check_types.py:485` (`now_iso = datetime.now(UTC)` on the worker), `:487-499` (future/staleness compare the source column to the worker's `now_iso`), `:509` (`age_h = now(UTC) - latest_dt` — worker clock minus source MAX). *(Surfaced by the adversarial completeness sweep; distinct from CONC-8, which is only app-internal aware/naive tz.)*
- **Problem:** Staleness age and the "future rows" classification subtract the source DB's stored timestamps from the **worker's** wall clock. Clock skew (NTP drift, a source on a skewed clock, or local-time-stored-as-UTC) silently corrupts the signal — a fast worker clock marks fresh rows "future" and excludes them from the staleness MAX (can flip a fresh table to a false fail), a slow clock under-reports age and misses real staleness — and freshness feeds SLA/incidents.
- **Fix:** Anchor "now" to the source clock (SELECT the source `current_timestamp`, or compute age in-SQL), or add a configurable allowed clock-skew/grace window before classifying rows as future / failing on staleness; emit a metric/warning when source MAX exceeds worker-now beyond the grace; document the synchronized-clock requirement.
- **Validate:** With a worker clock skewed ahead of the source, fresh rows aren't classified "future" within the grace window and staleness age is correct.

---

## 4. Reliability & runtime operations (new)

> REL-1 is folded into T0-3; REL-2 is Tier-0 (T0-4); REL-4 is folded into T0-1; REL-14 is folded into SEC+13.

### REL-3 — `/health` is a static stub; no readiness; no api/worker compose healthchecks · P1 · Tracked
- **Evidence:** `backend/app/main.py:44-53` (returns static `ok` + `provider_info()`, touches no DB); `docker-compose.yml` (only `postgres` has a healthcheck; `frontend.depends_on: api` waits for start, not readiness).
- **Problem:** No liveness/readiness split — a replica with an unreachable DB reports "ok" and keeps receiving traffic that 500s; orchestrators can't restart a wedged worker.
- **Fix:** `/healthz` (liveness) + `/readyz` (`SELECT 1` with a short timeout → 503 on failure); a worker heartbeat/liveness; add `healthcheck:` to `api`/`worker` and switch `frontend.depends_on` to `condition: service_healthy`.
- **Validate:** Readiness → 503 with DB down while `/healthz` stays 200; compose flips `api` unhealthy when DB is down.

### REL-5 — Backend image runs as root, no HEALTHCHECK, single-stage, unpinned base · P1 · Tracked
- **Evidence:** `backend/Dockerfile:1-14` (no `USER`, no `HEALTHCHECK`, single-stage, `FROM python:3.12-slim` floating); `frontend/Dockerfile:1,8` (multi-stage but final nginx runs as root, no HEALTHCHECK, unpinned).
- **Problem:** Root containers widen RCE/escape blast radius (enterprise baseline); no image health signal; floating bases break reproducibility.
- **Fix:** Non-root `USER`; `HEALTHCHECK` hitting `/readyz` (backend) / `/` (frontend); pin bases by digest; multi-stage backend (wheel build → slim runtime). (Image scanning → OPS-5.)
- **Validate:** `id` is non-root; `docker inspect` shows healthcheck; digest pinned.

### REL-6 — No global exception handler; 500s lack request-id + consistent body · P1 · Tracked
- **Evidence:** No `@app.exception_handler`/`add_exception_handler` anywhere; `backend/app/observability.py:129-161` sets `X-Request-ID` only on successful responses — an unhandled error returns a bare 500 with no request id and an inconsistent body. Also covers **API-1** (422 array vs business-error string `detail`).
- **Problem:** A user-reported 500 can't be tied to its structured log line (defeating the request-id plumbing); inconsistent error shapes break frontend handling; borderline info-leak if `debug` flips on.
- **Fix:** Register handlers for `HTTPException` + `Exception` (+ `RequestValidationError`) returning `{error:{code,message}, request_id}` and always setting `X-Request-ID` (pull `rid` from `request_id_var`); keep `HTTPException` status codes.
- **Validate:** A raised `ValueError` returns JSON with a `request_id` matching the log; `HTTPException(404)` stays 404.

### REL-7 — No retry/circuit-breaker on source DB queries (dead source hammered) · P1 · Tracked
- **Evidence:** `backend/app/connectors/sa.py:201-238` (single execute, no retry); `runner.py:194-234` (one error run, no backoff); `scheduler.py:135-153` (re-runs failing checks every poll); no `circuit/breaker/cooldown` anywhere.
- **Problem:** A down source makes every check error, opens/bumps incidents, and re-queries every 15s — turning an outage into self-inflicted load and escalation churn; transient blips error with no retry.
- **Fix:** Per-connection circuit breaker (open after N consecutive failures, cooldown, half-open; mark runs "source unavailable" without re-querying) + one jittered retry for transient connect errors; breaker-state gauge.
- **Validate:** Simulate a refused connection → checks stop hitting the source after the threshold, resume after cooldown, no error-run storm.

### REL-8 — No retry/circuit-breaker on notification channels · P1 · Tracked
- **Evidence:** `backend/app/core/notify.py:468-492` (`_deliver` one-shot try/except, no retry); each channel calls `raise_for_status()` once; `incidents.py:279-306` drops a failed dispatch. (Timeouts **are** set — `notify.py:34` `_SEND_TIMEOUT=10` — so this is retry/breaker, not timeout.)
- **Problem:** A transient 429/5xx permanently loses that incident's alert (operators miss real incidents); a persistently-dead channel is retried on every incident, each paying the full 10s timeout in a worker thread.
- **Fix:** Bounded retry-with-jitter on retryable statuses (honor `Retry-After`); per-channel-target circuit breaker; optionally a small durable outbox for lost-on-transient.
- **Validate:** 429-then-200 → one retry succeeds; persistent failure → breaker opens and short-circuits without the 10s wait.

### REL-9 — Agent tool-call handlers run with no per-call timeout · P1 · Tracked
- **Evidence:** `backend/app/llm/client.py:197-220` and `chat_agent.py:~582-591` invoke `handlers[call.name](...)` un-timed; LLM API calls + MCP connector are bounded (`providers.py:93-102,147-159`), but local tool handlers (source SQL etc.) are not.
- **Problem:** A slow tool query inside a chat/RCA turn pins an executor thread past the LLM timeout; the turn is bounded only by turn-count, not wall-clock; several dialects have no source statement timeout (backstop missing).
- **Fix:** Wrap tool-handler invocation in a bounded executor with a configurable per-tool timeout (`DQ_TOOL_CALL_TIMEOUT_SECONDS`), returning a tool-error on timeout.
- **Validate:** A deliberately slow handler → turn returns a timeout tool-error within the bound.

### REL-10 — WebSocket has no app-level idle/read timeout · P2 · Tracked
- **Evidence:** `backend/app/api/chat.py:160-172` (`receive_json()` in `while True`, no `wait_for`); relies on `nginx.conf:29` `proxy_read_timeout 300s` — absent behind a different proxy, idle sockets accumulate; no ping/keepalive.
- **Fix:** Wrap `receive_json()` in `asyncio.wait_for(idle_timeout)`, close on idle; optional ping frames; configurable timeout.
- **Validate:** Idle past the timeout → server closes the socket.

### REL-11 — No per-check wall-clock budget in worker (slow check starves pool) · P2 · Tracked
- **Evidence:** `scheduler.py:165` (fixed pool of 4) + `:153` (submit with no timeout); `runner.py:184-267` (no overall deadline; bounded only by per-dialect source timeouts, present only for Postgres).
- **Problem:** A check on a dialect without a statement timeout (MySQL/MSSQL/Snowflake/BigQuery/Trino/ClickHouse) can run arbitrarily long, occupying one of four slots; four slow checks block all scheduling throughput.
- **Fix:** A configurable per-check execution deadline enforced in the worker (cancel/abandon, mark `error: timeout`), independent of dialect support; pair with T0-4 + cross-dialect source timeouts.
- **Validate:** A check that sleeps past the deadline is marked timed-out and frees the slot.

### REL-12 — Auto-migrate-on-boot unsafe for multi-replica rolling deploys · P2 · Tracked
- **Evidence:** `backend/app/main.py:20` + `worker.py:18` both run `init_db()` → `command.upgrade(head)` (`db.py:59-91`); concurrency is handled (advisory lock) but migrate-on-boot is not separable from serve.
- **Problem:** During a rolling deploy, the first new replica mutates the shared schema while old replicas still serve — safety depends entirely on every migration being backward-compatible, with nothing enforcing/documenting it; a bad migration can crash-loop all replicas.
- **Fix:** A discrete migrate step (one-shot job the app waits on) + `DQ_AUTO_MIGRATE=false` for orchestrated deploys; document the expand/contract (backward-compatible) requirement; keep the advisory lock; CI lint flagging destructive migration ops.
- **Validate:** Deploy job migrates once; replicas boot with auto-migrate off and serve.

### REL-13 — `.dockerignore` doesn't exclude `.git`/`.env` (latent leak) · P3 · Tracked
- **Evidence:** `backend/.dockerignore` and `frontend/.dockerignore` are minimal (no `.git`/`.env`/`.venv`); safety currently rests on narrow `COPY` lists (backend) / the secret not reaching the final stage (frontend).
- **Fix:** Add `.git`, `.env`, `.env.*`, `*.local`, `.venv`, `*.db`, `*.sqlite` to both `.dockerignore` files (defense-in-depth, not COPY discipline).
- **Validate:** `docker history`/layer inspection shows no `.env`/`.git` content.

---

## 5. Security & authorization

> SEC+1/SEC+2 → T0-1; SEC+9 → T0-5 (full write-ups above). SEC-1/SEC-4 are positive (preserve).

### SEC-2 — DSNs stored plaintext at rest · P1 · Tracked (#24)
- **Evidence:** `backend/app/models.py:51` (DSN plaintext). **Extend the at-rest encryption to SEC+7 (MCP `auth_token`)** and any other DB-stored secret.
- **Fix:** Encrypt DSNs at rest / external secret store; masked-display vs stored secret; key rotation. **Validate:** audit logs + API responses never expose credentials; backups don't expose plaintext post-migration.

### SEC-3 — JWT in localStorage has tradeoffs · P2 · Tracked
- **Evidence:** `frontend/src/api/client.ts:5,15,19`. More XSS-exposed than HttpOnly cookies (mitigated by the clean markdown rendering — see Corrections). **Fix:** if the threat model requires, move to HttpOnly cookies + CSRF; at minimum keep token lifetime short and harden CSP (SEC+6). If cookie auth lands, tighten CORS (SEC+10).

### SEC+3 — No login rate-limiting / brute-force protection / lockout · P1 · Tracked
- **Evidence:** `backend/app/api/auth.py:18-27` (audit row on failure, no throttle/lockout); no rate-limit middleware; `models.User` has no `failed_login_count`/`locked_until`. (Subsumes **API-7**.)
- **Fix:** Per-account + per-IP throttling on `/auth/login` (sliding window / slowapi); progressive lockout (`failed_login_count`/`locked_until`, reset on success); identical 401 to avoid enumeration; configurable limits.
- **Validate:** N rapid wrong attempts → 429/locked; correct password during lockout still fails; counter resets after cooldown; response shape doesn't distinguish locked vs wrong.

### SEC+4 — Stateless JWT — no revocation, logout, or refresh · P1 · Tracked
- **Evidence:** `backend/app/security.py:31-55` (token has no `jti`; only `is_active` checked); `chat.py:99-111`; no `logout/refresh/revoke/jti/token_version` anywhere; `access_token_hours=12`; password change doesn't invalidate tokens.
- **Fix:** `token_version` column in the JWT (bump on logout-all / password change / revoke); `POST /auth/logout` + admin "revoke sessions"; optionally short access tokens + refresh; at minimum invalidate on password change.
- **Validate:** Change password / logout → old token rejected; normal tokens still work.

### SEC+5 — SSRF via user-supplied notification & MCP URLs (and DSN hosts) · P1 · Tracked
- **Evidence:** `backend/app/core/notify.py:112,168,216` (`httpx.post` to `rule.target` set by `api/notifications.py:54-112`, no host/scheme/IP validation); `backend/app/api/mcp.py:32-33` (only `http(s)://` checked); `backend/app/llm/client.py:55-59` (URL+token handed to the MCP connector); DSN host (`connectors/sa.py:62-75`, `connections.py:53-86,76-122`). Test endpoints (`notifications.py:128-153`, `connections.py:76-122`) make these one-click blind-SSRF oracles.
- **Problem:** An admin (or a forged-admin token via T0-1) can point a webhook/MCP/connection at `169.254.169.254`, loopback, or RFC1918 → cloud-metadata credential theft, internal port scanning.
- **Fix:** A shared egress guard used by every outbound call: resolve host, reject loopback/link-local/private/reserved (incl. `169.254.0.0/16`), non-http(s), and non-standard ports unless allowlisted; validate at create/update **and** send time (TOCTOU); `https://`-only for MCP. Disable IMDSv1 at infra as defense-in-depth.
- **Validate:** Guard unit tests against metadata IP / loopback / private / DNS-rebinding / `file://`; create/test endpoints reject internal targets.

### SEC+6 — No security response headers · P1 · Tracked
- **Evidence:** `backend/app/main.py:33-40` (only RequestContext + CORS); `frontend/nginx.conf` (gzip only, no `add_header`).
- **Problem:** No CSP/`X-Frame-Options`/`nosniff`/HSTS/`Referrer-Policy` — compounds SEC-3 (any XSS reads the localStorage token; clickjacking; SSL-strip).
- **Fix:** Add headers at the nginx edge (CSP report-only → tighten `default-src 'self'`, `frame-ancestors 'none'`; `X-Frame-Options: DENY`; `nosniff`; `Referrer-Policy: no-referrer`; HSTS when TLS-terminated) + a small FastAPI middleware for no-nginx runs.
- **Validate:** `curl -I` asserts headers; no CSP violations after tightening; app can't be framed.

### SEC+7 — MCP `auth_token` stored plaintext (secrets-at-rest, beyond DSN) · P2 · Tracked
- **Evidence:** `backend/app/models.py:444` (`auth_token` Text, masked in API but cleartext at rest); `api/mcp.py:36-48,61-69`; read back at `llm/client.py:57-58`.
- **Fix:** Encrypt with the same mechanism chosen for SEC-2; decrypt only at send time.
- **Validate:** Raw DB row is ciphertext; the LLM call still gets the decrypted token; API still masks.

### SEC+8 — Swagger `/docs` + OpenAPI exposed unauthenticated in prod · P2 · Tracked
- **Evidence:** `backend/app/main.py:30-31` (`docs_url`/`openapi_url` unconditional, no auth).
- **Fix:** Gate behind `DQ_ENV` (None in prod) or protect at the nginx edge; keep open in dev.
- **Validate:** prod → 404/401; dev → renders.

### SEC+10 — CORS `allow_credentials` with wildcard methods/headers · P3 · Tracked
- **Evidence:** `backend/app/main.py:34-40` (origins correctly allowlisted, but `allow_methods/headers=["*"]` + `allow_credentials=True`).
- **Problem:** Low risk today (bearer token, not cookies; origins pinned) but becomes a real CSRF surface if cookie auth lands (SEC-3).
- **Fix:** Narrow methods to those used and headers to `Authorization, Content-Type, X-Request-ID`; re-evaluate credentials with cookie auth.
- **Validate:** SPA still works cross-origin; non-allowlisted origin rejected; only enumerated methods/headers advertised.

### SEC+11 — Unbounded password → bcrypt 72-byte silent truncation · P3 · Tracked
- **Evidence:** `backend/app/security.py:20-21` (no length pre-check); `schemas.py:24-25,58` (`password` has `min_length` but no `max_length`).
- **Problem:** bcrypt ignores bytes past 72 — two long passphrases sharing a 72-byte prefix authenticate interchangeably; modern bcrypt can also raise on >72 bytes → latent 500.
- **Fix:** `max_length=72` on the password fields, or pre-hash with SHA-256 before bcrypt (supports arbitrary length); apply in both hash + verify.
- **Validate:** Two 80-char passwords differing after byte 72 don't both authenticate; long passphrase sets+verifies; no exception on huge input.

### SEC+13 — Chat WebSocket passes JWT as `?token=` query param · P2 · Tracked
- **Evidence:** `backend/app/api/chat.py:114-117`; `frontend/src/lib/useChatSocket.ts:24`; `frontend/nginx.conf:21-30` (proxy access logs include query strings). Also: `chat.py:104-108` only catches `PyJWTError`, so a malformed-but-signed `sub` raises after `accept()` (REL-14).
- **Problem:** A full-privilege, non-revocable (SEC+4) 12h token lands in proxy/LB/CDN access logs, browser history, and `Referer`.
- **Fix:** Authenticate via `Sec-WebSocket-Protocol` subprotocol or a short-lived single-use WS ticket (authenticated REST mints a ~30s nonce); broaden the `_ws_user` except to `(KeyError, ValueError)` → clean 4401; scrub `token` from proxy logs.
- **Validate:** WS still authenticates via the new transport; no JWT in access logs; malformed `sub` → clean close.

> **Privacy & data-protection** (SEC+14/SEC+15) and **encryption-in-transit** (SEC+16) were surfaced by the adversarial completeness sweep over this document — they qualify the SEC-4 "LLM privacy" positive (which holds *only* on the LLM path).

### SEC+14 — PII redaction is LLM-only; raw violating-row PII is persisted, served & exported · P1 · Tracked
- **Evidence:** `backend/app/core/runner.py:98,117` (`row_data=row` persisted verbatim); `backend/app/models.py:158` (`pii_columns` comment: "redacted in LLM prompts"); `backend/app/llm/client.py:100-114` (`redact_rows` is the only redactor — applied only on LLM paths: `chat_agent.py:483,503`, `rca_agent.py:142`, `explorer.py:61`); `backend/app/api/serialize.py:54-55` + `schemas.py:682` (`ExceptionOut.row_data` returned to any reader, no redaction); `backend/app/api/exceptions_api.py:288` (CSV export dumps `row_data` per row).
- **Problem:** `pii_columns` is honored only before data reaches the LLM. The same raw violating rows — including PII-marked columns — are written verbatim into `exception_records.row_data` (a millions-row table), returned verbatim by the `/exceptions` API to any viewer/editor, and dumped verbatim into the CSV export. Marking a column PII gives a false sense of protection: every violation copies that PII into storage, the browser, and downloads with no masking, elevated role gate, or view-audit.
- **Fix:** Honor `pii_columns` on the **storage and egress** paths — redact/hash/tokenize PII columns in `row_data` before persist (keeping fingerprints stable), or redact at `exception_out` + CSV-export time using the dataset's `pii_columns`; gate any raw-PII view/export behind an elevated role + audit event; add a "store raw vs redacted" flag. Document that `pii_columns` now governs storage/UI/export, not only prompts.
- **Validate:** A check on a PII-marked column → the persisted `row_data`, the `/exceptions` response, and the CSV export all mask the PII column for non-elevated roles; LLM redaction still works.

### SEC+15 — No subject-level right-to-erasure for PII copied into exception rows · P2 · Tracked
- **Evidence:** `backend/app/core/deletion.py:9-74` (`cleanup_dataset_dependents` — the only bulk purge, keyed on `dataset_id`); only `audit_log` has retention (`scheduler.py:67-88`); `row_data` persisted at `runner.py:98,117` with no per-subject addressability; no erasure/anonymize endpoint exists.
- **Problem:** Violating rows snapshot personal data into `row_data`; the only way to remove an individual's data is to delete the whole dataset (and all its history). There is no way to find/purge/anonymize one data subject across retained `exception_records`/`exception_events`/`rca_sessions`/`chat_messages` — non-compliant with GDPR/CCPA right-to-erasure. Compounds SEC+14.
- **Fix:** Provide a subject-erasure path (best: don't persist PII per SEC+14; otherwise an admin op that scrubs/anonymizes matching values across the retained tables, with an audit entry) + per-table retention/TTL (SCALE-6 covers space, not erasure); document the data-subject-deletion runbook (OPS-17).
- **Validate:** An erasure op removes/anonymizes a subject's values across exception/event/RCA/chat history and records an audit event.

### SEC+16 — Encryption-in-transit not enforced (source DB / app DB / SMTP) · P2 · Tracked
- **Evidence:** `backend/app/connectors/dialects.py:45-69` (PG/MySQL options set read-only + timeout but **no** `sslmode`/ssl); `backend/app/db.py:34` + `docker-compose.yml:27` (app-DB DSN, no `sslmode`); `frontend/nginx.conf:10,22` (`listen 80`, `proxy_pass http://api:8000`); `backend/app/core/notify.py:137-142` (`smtp.starttls()` with no `ssl.SSLContext`, not required); `backend/app/config.py:106` (`smtp_starttls` optional).
- **Problem:** Source credentials + source PII traverse the network in cleartext unless an operator manually appends `?sslmode=require` per DSN — nothing defaults to or validates TLS; the app-DB and nginx→api hops are plaintext by default; SMTP `starttls()` passes no SSL context (no cert verification → MITM with any cert) and STARTTLS isn't required (downgrade-strippable), leaking SMTP creds + incident contents. SEC-2/SEC+7 cover secrets-at-rest; in-transit was never reviewed.
- **Fix:** Default source + app-DB engines to TLS-required with an explicit opt-out, and validate the negotiated connection is encrypted for remote hosts at connection-test time; pass `ssl.create_default_context()` to `starttls()` + a "require TLS" setting that aborts the send if STARTTLS is unavailable (or implicit TLS on 465); document the TLS posture (loopback-only plaintext).
- **Validate:** A remote source/app-DB connection without TLS is rejected (or warned) per policy; SMTP send fails closed when STARTTLS is unavailable and verifies the server cert.

### OPS-18 — `/metrics` unauthenticated on the public API port · P2 · Tracked
- **Evidence:** `backend/app/main.py:57` (no auth — by design, AGENTS.md:197) but `docker-compose.yml:42-43` publishes `8000:8000`; "keep it network-internal" is doc-only and unenforced.
- **Problem:** Anyone reaching `:8000` reads `/metrics` (model names, SLA ids, traffic shape) — recon aid; many deployments copy the compose and expose `:8000`.
- **Fix:** Separate internal port (as the worker does with `:9100`), or bearer/allow-list, or block `/metrics` on the public vhost via nginx; don't publish the raw API port (route through the frontend proxy).
- **Validate:** `curl :8000/metrics` from outside the internal network is refused in the hardened topology.

---

## 6. LLM/agent risk & API robustness (new)

### LLM-1 — No instruction/data separation → prompt injection → false RCA · P1 · Tracked
- **Evidence:** `backend/app/llm/prompts.py:107-149,228-258,204-225`; `chat_agent.py:244-323` (interpolates knowledge `business_context`/`known_issues`/`notes`, profile top-values, exception `row_data`); `client.py:85-97` (`format_rows` feeds raw cells back).
- **Problem:** Source-controlled strings + user notes are concatenated into prompts with no delimiting/escaping; PII *redaction* doesn't stop instruction-bearing text. SQL writes are blocked, but injection can produce **false RCA reports** engineers act on, or drive exfiltration via read-only tool calls (LLM-3).
- **Fix:** Wrap all source-derived/user content in explicit `<untrusted_data>` delimiters with a standing "data, never instructions" rule (in `knowledge_block`, `format_rows`, `_dataset_overview`, `rca_user_prompt`); add an output-grounding check that the report/answer references an executed query; document injection as a known threat.
- **Validate:** A seeded injection string is wrapped in the data delimiter (FakeProvider test asserting the framing).

### LLM-2 — No per-user/tenant rate limit or token-cost budget on agent endpoints · P1 · Tracked
- **Evidence:** `backend/app/config.py:50-53` (only per-loop turn caps); `api/rca.py:19-59` (editor, no throttle, spawns a background loop); `api/chat.py:114-228` (only an in-session `_turn_lock`); no limiter middleware; tokens metered (`dq_llm_tokens_total`) but never enforced.
- **Problem:** An editor can loop `POST /rca/start` (each ≤12 turns × 16k tokens), open many chat sessions, hammer `/query/suggest`/dashboard-gen → unbounded provider billing + RCA background tasks have no concurrency cap (can saturate threads + provider rate limits).
- **Fix:** Per-user (+global) rate limit + rolling token/cost budget → 429; cap concurrent in-flight RCA sessions; short-TTL cache keyed on (dataset, run, question) for RCA/suggest.
- **Validate:** N+1th call in the window → 429; concurrent RCA beyond the cap rejected/queued.

### LLM-3 — Chat tools exfil + bypass per-resource authz; `render_chart` 500 vs 200 cap · P1 · Tracked (authz → T0-5)
- **Evidence:** `chat_agent.py:46-63` (`run_sql` takes any `connection_id`), `:479-519` (`render_chart` → `execute_panels`), `:194-198` (`_connector` loads any Connection, no ownership check); `core/adhoc.py:16,120-136` (`PANEL_ROW_CAP=500` vs `agent_query_row_limit=200`); `api/chat.py:122-125` (WS gate = editor only).
- **Problem:** Two issues: (1) tool calls reach any connection (authz — see T0-5); (2) `render_chart` returns up to 500 rows (2.5× the SQL-tool cap), a wider exfil channel, model-controlled aliasing.
- **Fix:** Enforce the agent row cap on `render_chart`; authorize tool calls per connection once the ACL model lands (T0-5); bound chart output rows/columns fed back to the model.
- **Validate:** `render_chart` truncates at the agent cap; a tool call against an unauthorized connection is rejected.

### LLM-5 — Model id unpinned; adaptive-thinking gated by a brittle version regex · P3 · Tracked
- **Evidence:** `backend/app/config.py:49,75`; `providers.py:32` (`_ADAPTIVE_RE = (fable|opus-4-[6-9]|sonnet-4-[6-9])`) used at `:139`.
- **Problem:** No pinned/validated model registry; a future `opus-4-10` won't match `4-[6-9]` → silently loses adaptive thinking; an unknown id fails only at call time.
- **Fix:** Replace the regex with a capability map / version-floor handling multi-digit minors; validate `DQ_LLM_MODEL` at startup; record the resolved model in output.
- **Validate:** Capability check across `opus-4-8`/`opus-4-10`/`sonnet-4-9`/unknown.

### API-2 — `/api/v1` with no versioning/deprecation strategy · P2 · Tracked
- **Evidence:** `backend/app/main.py:41-42` (hardcoded `/api/v1`); golden rule #3 mandates coordinated breakage, not versioning; `schemas.py:671` even reshapes "v2" inside `/v1`.
- **Fix:** Additive-only within `/v1`; `/v2` for breaking changes; `Deprecation`/`Sunset` headers; document contract stability. **Validate:** a contract test fails if a response field is removed without a version bump.

### API-3 — No request body-size limit; no `max_length` on SQL/free-text · P2 · Tracked
- **Evidence:** No body-size middleware; `schemas.QueryRunIn.sql:798`, `SuggestIn.goal:829`, `KnowledgeIn.*:399-411`, `SavedQueryCreate.sql:858` unbounded; `guard_sql` regex-scans the whole string (chat WS content *is* capped at 8000, `chat.py:191`).
- **Fix:** A max-body-size guard (middleware / reverse proxy) + `max_length` on `sql`/`goal`/knowledge free-text. **Validate:** oversized body → 413/422; `max_length` rejection on `QueryRunIn.sql`.

### API-4 — Many list endpoints unbounded (no pagination/cap) · P2 · Tracked
- **Evidence:** Unbounded `.all()`: `datasets.py:23`, `checks.py:38`, `connections.py:19,44`, `auth.py:35,40`, `notifications.py:48`, `contracts.py:70,269`, `sla.py:52`, `scorecards.py:85`, `custom_dashboards.py:147`, `mcp.py:21`, `docs.py:52`. Hard-capped without offset (drops rows past N): `saved_queries.py:69`, `chat.py:57`, `adhoc_dashboards.py:108`, `rca.py:71`. (Distinct from PERF-8, which is about the already-paginated runs/exceptions.)
- **Fix:** `limit`/`offset` (or cursor) + `total` on high-cardinality lists; document genuinely-small fixed sets as bounded. **Validate:** each list accepts `limit`/`offset` + returns a total; row N+1 reachable on currently-capped lists.

### API-5 — No idempotency keys on creating POSTs · P2 · Tracked
- **Evidence:** `rca.py:19-59` (every POST creates a new session + background agent — worst case, doubles spend); `saved_queries`/`custom_dashboards`/`adhoc_dashboards` creates; only connections/users dedupe by natural key. No `Idempotency-Key` anywhere.
- **Fix:** Optional `Idempotency-Key` header on create endpoints (at least `/rca/start` + dashboard/check creators); store + replay within a window. **Validate:** two POSTs with the same key → one resource, same id.

### API-6 — No caching headers / ETags on read-heavy GETs · P3 · Tracked
- **Evidence:** `query.py:65-91` (schema/ddl), `insights.py`, `dashboard.py`, `scorecards.py:85` — no `ETag`/`Cache-Control`/`If-None-Match`.
- **Fix:** `ETag`/`Last-Modified` + `Cache-Control` on stable read-heavy GETs (schema tree, insights, rollups); honor `If-None-Match` (also relieves PERF-11). **Validate:** GET returns an `ETag`; follow-up `If-None-Match` → 304.

---

## 7. Backend structure and maintainability

### BE-1 — `schemas.py` is a cross-domain contract monolith · P1 · Tracked
- **Evidence:** `backend/app/schemas.py` (>1,100 lines spanning auth/connections/datasets/profiles/scorecards/SLA/knowledge/checks/monitor-packs/contracts/runs/incidents/exceptions/RCA/chat/query/saved-queries/notifications/audit/dashboards/lineage/search/docs/widget-policy/insights).
- **Fix:** Split by domain (`app/schemas/auth.py`, `datasets.py`, …) with a compatibility barrel in `__init__.py`; move quota/policy constants to domain modules. **Validate:** OpenAPI stable unless intentionally changing; backend tests + frontend build after each split.

### BE-2 — `models.py` is a growing metadata-model monolith · P1 · Tracked
- **Evidence:** `backend/app/models.py` (>500 lines, all ORM models). **Fix:** central `Base`, split classes by domain (`app/models/checks.py`, …) with `__init__.py` re-exporting all; careful Alembic autogenerate tests. **Validate:** `tests/test_migrations.py` green; Alembic sees all models.

### BE-3 — Core depends on API serialization (inverted dependency) · P1 · Tracked
- **Evidence:** `backend/app/core/contracts.py:17` imports `from app.api.serialize import check_out`. **Fix:** move shared projection into a neutral module (`app/core/projections.py`); core returns a domain dict/DTO, API converts. **Validate:** an import-boundary test (`core` must not import `app.api`).

### BE-4 — API routers contain too much business logic · P1 · Tracked
- **Evidence:** `api/dashboard.py`, `exceptions_api.py`, `datasets.py`, `custom_dashboards.py`, `connections.py`, `query.py` (routers build complex queries, run connectors, do exports + policy). **Fix:** lightweight router/service/repository split, starting with high-churn areas (exceptions, dashboard/insights, checks/runs, datasets/profiling). **Validate:** service-level unit tests for query/filter; endpoint tests for auth + shape.

### BE-5 — `check_types.py` is a god module · P1 · Tracked
- **Evidence:** `backend/app/core/check_types.py` (>1,100 lines: param metadata, validation, SQL gen, regex fallback, row-count anomaly, freshness, custom SQL, ML, drift, schema-change, registry). **Fix:** split by family (`core/checks/{base,registry,simple_sql,freshness,volume,custom_sql,ml,drift,schema}.py`), keep `CHECK_TYPES`/`validate_check`/`run_check_type` public from a compat module; shared `_sample_where` into a small exec util. **Validate:** `tests/test_checks.py` passes unchanged; per-family tests added.

### BE-6 — Router mounting is slightly irregular · P3 · Tracked
- **Evidence:** `backend/app/main.py:41-42`; `api/lineage.py:1,16` mounts separately (spans dataset+connection paths). **Fix:** include lineage in `api/__init__.py` like the others (or split `dataset_lineage.py`/`connection_lineage.py`). **Validate:** OpenAPI route list identical.

### BE-7 — Source connector layer needs capacity controls · P1 · Tracked
- **Evidence:** `backend/app/connectors/{sa,dialects,safety}.py`. Strengths: `guard_sql()`, read-only engines, per-connection engine cache, centralized dialects, explicit PG/MySQL pools. Gaps: source pools and worker concurrency independent; no app-level per-source concurrency cap; some dialects use default pool behavior; statement timeout only on Postgres.
- **Fix:** per-source concurrency semaphores keyed by connection id; dialect-specific statement/query timeouts; source pool settings in config; track source query queueing time. (Overlaps SCALE-3, REL-7, REL-11.) **Validate:** two checks on one connection honor the cap; metrics by connection *kind*, not raw name.

---

## 8. Frontend structure and maintainability

### FE-1 — Routes are eagerly imported · P1 (as app grows) · Tracked
- **Evidence:** `frontend/src/App.tsx:6,28`. **Fix:** `React.lazy` + `Suspense` route-splitting (keep Login + shell eager; lazy Workbench/Settings/Assistant/Lineage/CustomDashboards). **Validate:** `npm run build` chunk sizes; smoke that lazy routes load. *(Coordinate with the v2 design reskin — split + reskin each surface once.)*

### FE-2 — Large pages own too many responsibilities · P1 · Tracked
- **Evidence:** `WorkbenchPage.tsx`, `SettingsPage.tsx`, `HomePage.tsx`, `CustomDashboardPage.tsx`, `IncidentsPage.tsx` (params + fetch + mutate + invalidation + state + forms + tables + layout + rules). **Fix:** feature folders (`features/{workbench,settings,home,incidents}/` with `hooks.ts`, presentational components, route component); extract Settings panels (Audit/MCP/NotificationRules/Users) + Workbench internals (SchemaBrowser/SavedQueries/History/Toolbar/ResultPanel). **Validate:** component tests for extracted forms; route smoke. *(Add tests before decomposition — TEST-2.)*

### FE-3 — API types manually mirrored; duplicate `ExceptionPage` · P1 · Tracked
- **Evidence:** `frontend/src/api/types.ts:439,965` (`ExceptionPage` declared twice; legal via declaration merging). **Fix:** remove the duplicate; split `types.ts` by domain; longer-term generate TS from the FastAPI OpenAPI (see OPS-15). **Validate:** `npm run typecheck`; CI check that generated types are current.

### FE-4 — API calls not encapsulated by feature hooks · P2 · Tracked
- **Evidence:** `frontend/src/api/client.ts` + many `useQuery`/`useMutation` inline. **Fix:** feature hooks (`useDatasets`, `useExceptions(filters)`, …) + a `queryKeys` factory; co-locate invalidations with mutations. **Validate:** typecheck; hook tests where high-value.

### FE-5 — Styling centralized; inline styles common · P2 · Tracked
- **Evidence:** `frontend/src/styles.css` + many `style={{…}}`. **Fix:** split CSS by feature/category (`styles/{base,layout,tables,forms}.css`, `features/.../x.css`); replace repeated inline styles with utility classes/props; **keep design tokens in one place** (the v2 token system is the natural home). **Validate:** Playwright screenshots for core screens; mobile/desktop breakpoints.

### FE-6 — Frontend lacks test coverage · P1 · Tracked
- **Evidence:** no `*.test.*`/`*.spec.*` under `frontend/src`. **Fix:** Vitest + React Testing Library (start with `api/client` error handling, `CheckParamsForm`, exception-filter URL behavior, workbench tab persistence, widget-config validation); Playwright smoke (login, generate checks, run check, triage, workbench query). **Validate:** CI runs frontend unit tests + build. (See OPS-8.)

---

## 9. Frontend a11y, client-security & UX robustness (new)

### FEX-1 — Navigation `<tr onClick>` rows are keyboard-inoperable · P1 · Tracked
- **Evidence:** `ChecksTable.tsx:190,291`, `RunsTable.tsx:34`, `DatasetsPage.tsx:199`, `ConnectionsPage.tsx:240`, `ConnectionDetailPage.tsx:157`, `IncidentsPage.tsx:393`, `ConnectionBrowsePage.tsx:106` (`.clickable` rows, no `tabIndex`).
- **Problem:** Keyboard/SR users can't open a check/run/dataset/connection/incident — the core drill-down. WCAG 2.1.1 failure across the most-used screens.
- **Fix:** Make the primary cell a real `<Link>` (best), or add `role="button" tabIndex={0} onKeyDown` (helper `activateOnKey` exists at `components/ui.tsx:7`); keep inner action buttons' `stopPropagation`.
- **Validate:** Tab to each row → visible focus ring + Enter/Space opens; an RTL `keyDown{Enter}` test.

### FEX-2 — Tab strips have no tab semantics / arrow-key nav · P1 · Tracked
- **Evidence:** `DatasetDetailPage.tsx:120-126` (`<button class=tab>`, no `role=tablist/tab`, no `aria-selected`, no arrow keys); `WorkbenchPage.tsx:749` (`<div class=wb-tab onClick>`, no role/tabIndex).
- **Fix:** ARIA Tabs pattern (`role=tablist/tab/tabpanel`, `aria-selected`, roving `tabIndex`, Left/Right/Home/End); promote the Workbench `<div>` to `<button>`. **Validate:** SR announces "tab, selected, N of M"; arrows move tabs.

### FEX-3 — Chat session switcher is `<div onClick>`; composer unlabeled · P2 · Tracked
- **Evidence:** `AssistantPage.tsx:241-245` (`<div class=chat-session-item onClick>` with nested delete button), `:346-357` (textarea has only a placeholder).
- **Fix:** Make the session row a `<button>` (move delete to a sibling — don't nest interactive elements); add `aria-label`/visually-hidden label to the composer. **Validate:** Tab reaches each session + composer; both announce a name.

### FEX-4 — Streaming assistant output has no `aria-live` region · P2 · Tracked
- **Evidence:** `AssistantPage.tsx:281,313-317` (thread + thinking status stream with no `aria-live`; contrast `Spinner` `ui.tsx:221` which is correct).
- **Fix:** `role="log" aria-live="polite" aria-relevant="additions"` on the thread/status (keep verbose `<details>` out of the live region). **Validate:** SR announces new messages + thinking status.

### FEX-5 — Secondary `useQuery` calls ignore `isError` (silent failures) · P2 · Tracked
- **Evidence:** `ExceptionsPage.tsx:17`, `IncidentsPage.tsx:255` (datasets query, no `error`), `ProfileTab.tsx:69-77,95` (preview/exploration, no error path). (Most pages *do* handle `isError` — this is the residual minority.)
- **Fix:** Destructure `error`/`isError` → render `<ErrorBox>` / inline "couldn't load options". **Validate:** force 500 on `/datasets` + exploration → visible error, not a blank control.

### FEX-7 — `DocsPage` parses timestamps without UTC normalization · P3 · Tracked
- **Evidence:** `DocsPage.tsx:10-12` (`new Date(iso).toLocaleDateString`) bypasses the `endsWith("Z")` guard in `lib/format.ts:19,30,42,54`. **Fix:** route through a shared `format.ts` date formatter. **Validate:** with a non-UTC `TZ` and a no-Z `…T02:00:00`, the date matches UTC.

### FEX-8 — `fmtDateTime` omits the year (cross-year ambiguity) · P3 · Tracked
- **Evidence:** `lib/format.ts:20-25` (month/day/hour/minute only), used at `IncidentsPage.tsx:421,426`. **Fix:** include the year when not current (or always for absolute columns); keep the compact `timeAgo`. **Validate:** a last-year timestamp shows the year.

### FEX-9 — Global search dropdown lacks listbox/option semantics · P3 · Tracked
- **Evidence:** `Layout.tsx:247-279` (`.search-pop` plain div; results `<button>` with no `role=option`; input lacks `aria-activedescendant`/`aria-expanded`/`aria-controls`). Otherwise excellent (Cmd+K / `/` / arrows / Enter / Esc). **Fix:** `role=listbox` + `aria-expanded/controls` on input, `role=option id` per hit, `aria-activedescendant`. **Validate:** SR announces "listbox, N results" + reads the active option.

---

## 10. UI and product flows

The original review also flagged these from `docs/BROKEN-FLOWS.md` / `docs/UI-GLOSSARY.md`. **UI-1…UI-4 were since shipped** as the BF issues #74–#83 — verify before reopening.

- **UI-1 — Breadcrumbs / context return** · Done (#74). Compact breadcrumbs on deep screens; preserve origin params; standardized "Back to run/dataset/Open in workbench".
- **UI-2 — Destructive-action confirm/undo** · Done (#78). Route destructive actions through app `Modal` (not `window.confirm`); typed confirmation for cascading deletes; explicit Save for role changes.
- **UI-3 — Unsaved-work guards** · Done (#76). `useDirtyGuard` + modal/route/tab guards; autosave drafts for high-effort text.
- **UI-4 — Exceptions filter transparency** · Done (#75). Preserve unrelated query params; removable filter chips; URL-transition tests.
- **UI-5 — Disabled controls need recovery instructions** · P2 · Tracked. Disabled states should say what unlocks them ("Profile now" / "Ask admin" / "Configure LLM") with the next action nearby.

---

## 11. Testing and verification

### TEST-1 — Backend tests broad but SQLite-centered · P2 · Tracked
Strength: broad pytest across checks/API/lineage/exceptions/dashboards/notifications/scorecards/migrations/chat/contracts. Gap: prod is Postgres; performance/locking/dialect behavior differs. **Fix:** keep SQLite for speed; add a small Postgres CI suite for migrations, worker claim concurrency, indexes/plans, JSON behavior, and row-level locking once durable jobs land (PERF-5). Also add **security tests** — authz/IDOR (T0-5) and SSRF-guard (SEC+5). *(guard_sql fuzzing already exists — see Corrections.)*

### TEST-2 — Frontend has no unit/component tests · P1 · Tracked
**Fix:** add frontend tests **before** major page decomposition (FE-2); start with pure helpers + high-risk components; small Playwright smoke for core flows; add a11y assertions (`jest-axe`). (See OPS-8.)

### TEST-3 — Performance tests are missing · P1 · Tracked
**Fix:** a synthetic metadata load generator (N datasets, M checks, millions of runs/exceptions); source-table benchmarks (wide-table profiling, many checks/one table, ML memory/CPU, dashboard/insights latency); performance budgets (dashboard/exceptions/runs p95, worker backlog max age, max memory/worker). Add **migration downgrade tests** (OPS-16) and the schema↔types contract test (OPS-15).

---

## 12. CI/CD, supply chain, observability coverage & ops docs (new)

> Verified-good: `.gitignore` has never leaked a `.db`/`.env` (history checked); `/metrics` cardinality is route-templated; PII-in-logs is clean (path-only). See Corrections.

### OPS-1 — No software-composition / vulnerability scanning · P1 · Tracked
- **Evidence:** `.github/workflows/ci.yml` runs ruff + compileall + compose-config + json-validate + pytest (backend) and `npm ci` + build (frontend); no `pip-audit`/`npm audit`/CodeQL; no `.github/dependabot.yml` / `renovate.json`.
- **Fix:** Dependabot (pip/npm/github-actions/docker, weekly) + a CI job running `pip-audit` and `npm audit --audit-level=high`; optionally CodeQL. **Validate:** a vulnerable pin fails the SCA job; Dependabot opens PRs.

### OPS-2 — Python dependencies unpinned, no lockfile · P1 · Tracked
- **Evidence:** `backend/pyproject.toml:11-51` all `>=`; no `requirements.lock`/`uv.lock`; `backend/Dockerfile:8` resolves freshest at build. (Frontend has `package-lock.json` — good.)
- **Fix:** `uv`/pip-tools → committed lock; install from it in CI + Dockerfile; keep `pyproject` ranges for the library, pin the app. **Validate:** two builds → identical resolved versions.

### OPS-3 — No secret scanning in CI · P1 · Tracked
- **Evidence:** no gitleaks/trufflehog; `.env.example` carries many secret-shaped keys (raising paste risk). **Fix:** gitleaks/trufflehog job on push+PR + enable GitHub push-protection. **Validate:** a fake `sk-…` in a tracked file fails the job.

### OPS-4 — No SAST for the app's own code · P2 · Tracked
- **Evidence:** ruff is style/lint (`E,F,W,I,UP,B,C4`), not security; no bandit/semgrep/CodeQL — yet the safety story rests on `guard_sql`/JWT/bcrypt. **Fix:** Semgrep (`p/python`, `p/security-audit`, `p/javascript`) or CodeQL; baseline existing. **Validate:** a `subprocess(shell=True)` / f-string SQL is flagged.

### OPS-5 — Docker base images tag-pinned, not digest; no image scanning · P2 · Tracked
- **Evidence:** `backend/Dockerfile:1` `python:3.12-slim`, `frontend/Dockerfile:1,8` `node:22-alpine`/`nginx:1.27-alpine`; compose infra tag-pinned; no Trivy/grype. **Fix:** digest-pin bases (Dependabot `docker` bumps) + a Trivy CI job failing on HIGH/CRITICAL. (Complements REL-5.) **Validate:** reproducible build for a commit; Trivy runs.

### OPS-6 — No SBOM generation · P3 · Tracked
- **Fix:** syft (or cyclonedx) SBOMs in CI as artifacts/release attachments; pairs with OPS-5. **Validate:** `sbom.cdx.json` uploaded.

### OPS-7 — No test-coverage measurement or gate · P2 · Tracked
- **Evidence:** no `pytest-cov`/`--cov`; `htmlcov/` in `.gitignore` is vestigial. **Fix:** `pytest --cov=app --cov-report=xml` + `--cov-fail-under` floor (ratchet up); optionally Codecov. **Validate:** CI fails when a PR drops below the floor.

### OPS-8 — Frontend has no lint/test tooling (or a11y checks) in CI · P2 · Tracked
- **Evidence:** `package.json` scripts are dev/typecheck/build/preview only; no eslint/vitest/playwright/axe; CI runs only `npm run build`. **Fix:** ESLint (`@typescript-eslint`, `react-hooks`, `jsx-a11y`) + Vitest/Testing-Library substrate (hosts TEST-2) + optional `jest-axe`. **Validate:** `npm run lint` in CI fails a hook-deps violation.

### OPS-9 — CI never builds/scans/smoke-tests images; e2e smoke not in CI · P2 · Tracked
- **Evidence:** CI validates compose *config* only; never builds images, runs the stack, or runs `scripts/e2e_smoke.py`. **Fix:** a CI job that `docker compose up --build -d`, waits for `/api/v1/health`, runs the e2e smoke, tears down (also a Postgres integration signal — overlaps TEST-1); add image build/push to GHCR on tags. **Validate:** a broken migration / nginx WS config turns the job red.

### OPS-10 — GitHub Actions pinned to floating major tags · P3 · Tracked
- **Evidence:** `ci.yml:13,14,36` use `@v4`/`@v5`. **Fix:** pin to commit SHAs (Dependabot `github-actions` bumps) + add a least-privilege `permissions:` block. **Validate:** `grep '@v' .github/workflows/*.yml` is empty.

### OPS-11 — No branch-protection / required-status enforcement · P2 · Tracked
- **Evidence:** AGENTS.md says "CI gates" + "pushes to main are acceptable" — contradictory; branch protection isn't in-repo. **Fix:** require `backend`/`frontend` checks on `main`, require PRs, disallow direct pushes; document the rule. **Validate:** a red-CI PR can't merge; direct push rejected.

### OPS-12 — No scheduler backlog observability (queue depth / oldest-due) · P1 · Tracked
- **Evidence:** `observability.py:50-51` only `dq_worker_claims_total`/`dq_worker_up`; `scheduler.py:135-141` claims `LIMIT 20`/poll with no due-count or oldest-due gauge. **Fix:** in `poll_once`, set `dq_scheduler_due_checks` (count `status=active AND next_run_at<=now`) + `dq_scheduler_oldest_due_seconds`; dashboard panel (OPS-14) + alert source. **Validate:** seed N>20 due checks → gauges report true backlog + oldest age.

### OPS-13 — No per-user/feature LLM cost attribution · P2 · Tracked
- **Evidence:** `providers.py:72-73,82` count tokens/requests by provider/model only (no user/feature, no dollar metric). **Fix:** a bounded `feature` label (chat/rca/checkgen/explorer/workbench) + a `dq_llm_cost_usd_total` from a price map; persist per-user token totals in the DB for chargeback (avoid high-cardinality user labels in Prometheus). **Validate:** each feature increments its series; a cost panel returns non-zero.

### OPS-14 — Grafana dashboard missing notification/latency/incident panels · P3 · Tracked
- **Evidence:** `monitoring/grafana/dashboards/dq-sentinel.json` omits `dq_notifications_sent_total` (emitted `notify.py:483/486`) and the `dq_check_run_seconds`/`dq_source_query_seconds`/`dq_llm_request_seconds` histograms; incidents/scorecards/contracts have no instrumentation. **Fix:** add notification-success/failure-by-channel + latency p50/p95 panels (+ the OPS-12 backlog panels); instrument incident MTTR/escalation. **Validate:** `json.tool` passes (CI); panels render.

### OPS-15 — No backend-schema ↔ frontend-types contract test · P2 · Tracked
- **Evidence:** golden rule #3 mandates `schemas.py`↔`types.ts` parity but nothing tests it; `test_contracts.py` is the ODCS *product feature*, not the API↔TS contract (easy to conflate). **Fix:** CI dumps `app.openapi()` and either generates TS (`openapi-typescript`) + diffs against committed `types.ts`, or snapshots the OpenAPI JSON + fails on uncommitted change. **Validate:** a renamed `schemas.py` field without a types update fails CI.

### OPS-16 — No migration downgrade/rollback test · P2 · Tracked
- **Evidence:** `test_migrations.py` asserts forward parity + legacy stamping only; all 8 migrations define non-empty `downgrade()` but none are exercised. **Fix:** a test that walks `upgrade head → downgrade base → upgrade head` (assert schema parity each step); run on Postgres once TEST-1 lands (downgrade SQL is dialect-sensitive). **Validate:** a broken `downgrade` fails the test.

### OPS-17 — No SECURITY.md / threat model / runbook / DR / CONTRIBUTING / CHANGELOG · P1 · Tracked
- **Evidence:** repo has AGENTS.md/README/LICENSE + product docs only; absent: `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, threat-model/runbook/DR/capacity/on-call/release docs; README has no production-deploy/backup/TLS guidance.
- **Problem:** No coordinated-disclosure channel; the app DB (all checks/exceptions/audit/PII) has no documented recovery; the security surface (LLM-authored SQL, unauthenticated `/metrics`, JWT, shared-tenant model) is undocumented for reviewers — table-stakes for enterprise procurement.
- **Fix (priority order):** `SECURITY.md` (disclosure + supported versions); `docs/runbook.md` (worker-behind via OPS-12, `/metrics` containment, `database is locked`, LLM-503, notify-send failures via OPS-14); `docs/backup-restore.md` (Postgres `pg_dump`/restore + Alembic version handling — SCALE-9); `docs/threat-model.md` (trust boundaries); `CONTRIBUTING.md` (→ AGENTS.md) + `CHANGELOG.md`/release process. **Validate:** GitHub surfaces the policy tab; a restore drill follows the doc.

---

## 13. Coding principles currently weak or inconsistently applied

- **Single Responsibility** — weak in `check_types.py`, `schemas.py`, `models.py`, large frontend pages, `styles.css`. Split by domain; keep compatibility exports during migration.
- **Separation of Concerns** — routers do business logic + queries (BE-4); core imports API serialization (BE-3); frontend pages mix data/state/forms/layout (FE-2). Introduce services/repositories (backend) and feature hooks/presentational components (frontend).
- **Push work to the right layer** — dashboard/insights/scorecards bucket in Python (PERF-7); profiling uses many exact source queries (PERF-1). Push aggregation to the app DB; push only safe, budgeted work to source DBs; pull bounded, projected data into Python.
- **Explicit resource budgets** — worker has no durable backpressure (PERF-5); ML uses all cores (PERF-4); source concurrency uncapped per connection (BE-7); compose has no small-hw profile (PERF-12); no app-DB timeouts (T0-4). Add queue depth, leases, concurrency caps, timeouts, and resource settings; document small/medium/large sizing.
- **Fail safe / fail fast** *(new)* — config boots insecure rather than refusing (T0-1); no readiness gate (REL-3); no graceful shutdown (T0-3). Validate config at boot; gate traffic on readiness; drain on shutdown.
- **Least privilege / deny by default** *(new)* — authz is role-only with no object scoping (T0-5); SSRF egress is unrestricted (SEC+5); `/metrics`/`/docs` open (OPS-18/SEC+8). Default-deny on resource access and outbound egress.
- **DRY API contracts** — backend `schemas.py` ↔ frontend `types.ts` mirrored by hand (FE-3) with no test (OPS-15); duplicate `ExceptionPage`. Generate types from OpenAPI or enforce the contract in CI.
- **Test the highest-risk paths** — no frontend tests (TEST-2/FE-6), no perf tests (TEST-3), no security/authz tests (TEST-1), no Postgres concurrency tests. Add them; gate coverage (OPS-7).

---

## 14. Corrections & already-handled (do not re-litigate)

Areas explicitly checked and found **healthy** — don't spend effort here:

- **Source SQL guard is genuinely strong** (SEC-1) and already has bypass/fuzz tests (`backend/tests/test_safety.py` covers comment/literal/dollar-quote/bracket-identifier/multi-statement tricks). `enforce_limit` casts the limit with `int()` — injection-safe.
- **Frontend is more hardened than assumed:** a real `ErrorBoundary` wraps the app (`main.tsx:20`), `Modal` is fully focus-trapped, a global `:focus-visible` ring + `prefers-reduced-motion` guards exist, there's a skip-link + `<main>`, prod source maps are off (`vite.config.ts:20`), no stray `console.log`, and a 401 hard-clears the token. **Markdown is safe** — `react-markdown` v10 with **no `rehype-raw`** and no `dangerouslySetInnerHTML` anywhere, so the "XSS → token exfil" path does **not** exist today.
- **PII-in-logs is clean** — request log is path-only (no query string), DSNs/rows/SQL are never logged; only the bootstrap admin email is logged once at first boot.
- **Notification timeouts already exist** (`notify.py:34` `_SEND_TIMEOUT=10`) — the gap is retry/breaker (REL-8), not timeout.
- **LLM `safe_user_error` + markdown-fence parsing are correct** (`client.py:22-34,231-237`); OpenRouter's 200-with-error-payload is handled (`providers.py:260-269`); no raw-provider leak in WS/report paths found.
- **The Postgres migration boot-race is handled** by a session advisory lock (`db.py:83-91`); the `_ensure_columns` shim AGENTS.md warns about is **already gone** (fully on Alembic).
- **Role-based authz IS tested** where ownership exists (chat sessions, custom dashboards); the gap (T0-5) is the *absence of an object/tenancy model*, not missing tests — partially tracked by #26/#72.
- **`/metrics` cardinality discipline holds** (`observability.py:143-145` labels route templates, not raw paths).
- **Severity recalibrations:** CONC-3 from P0→P1 (prod Postgres enforces FKs; the gap is dev/prod divergence + retention readiness); SCALE-1/SCALE-2 are P1 today, **P0 only once the API is scaled past one replica**.

---

## 15. Remediation roadmap

**Tier 0 — production blockers (file + fix first).** T0-1…T0-5. A single prod-config validator closes T0-1; the rest are small, isolated correctness/security fixes. *Nothing should be exposed to a real multi-user/production environment until these land.*

**Phase 0 — quick wins & safety fixes.** Remove duplicate `ExceptionPage` (FE-3); lazy-load heavy routes (FE-1); `DQ_ML_N_JOBS=1` default (PERF-4); small-hardware compose override (PERF-12); obvious metadata indexes after plan review (PERF-6); move `core/contracts.py` off `api.serialize` (BE-3); query-count tests for runs/exceptions (PERF-8); frontend test setup + URL-filter test (TEST-2); add Dependabot + `pip-audit`/`npm audit` + gitleaks (OPS-1/2/3); `.dockerignore` hardening (REL-13).

**Phase 1 — metadata query scalability.** SQL-aggregate dashboard/insights/scorecards (PERF-7); cursor pagination for runs/exceptions (PERF-8); bulk-load serializers; retention settings + archival/partitioning (SCALE-6); scheduler backlog metrics (OPS-12); metadata load tests (TEST-3).

**Phase 2 — source query cost control.** Profile tiers + budgets (PERF-1); cost-aware distinct/top-values; row-count + table-fact cache (PERF-2); combine compatible checks; custom-SQL count modes (PERF-3); per-source concurrency + statement timeouts across dialects (BE-7/REL-7/REL-11); schema-tree cache (SCALE-8).

**Phase 3 — worker & deployment hardening.** Durable job table + leases + backlog coalescing + graceful shutdown (PERF-5 + T0-3); split maintenance jobs; readiness/liveness probes + compose healthchecks (REL-3); app-DB timeouts + pool tuning (T0-4/SCALE-7); image hardening (REL-5/OPS-5); migrate-as-a-step (REL-12); Postgres concurrency + downgrade tests (TEST-1/OPS-16); CI image build + e2e smoke (OPS-9).

**Phase 4 — security & multi-tenancy.** DSN + MCP-token encryption (SEC-2/SEC+7/#24); SSRF egress guard (SEC+5); security headers + CORS tightening (SEC+6/SEC+10); login throttling + token revocation (SEC+3/SEC+4); object-level authorization / tenancy (T0-5/#26/#72); LLM injection framing + per-user cost budget + tool authz (LLM-1/LLM-2/LLM-3); API robustness (error schema REL-6, body limits API-3, unbounded lists API-4, idempotency API-5); **PII-on-egress redaction + subject erasure + encryption-in-transit (SEC+14/SEC+15/SEC+16)**; ops docs (OPS-17).

**Phase 5 — structural refactor & a11y.** Split `schemas.py`/`models.py`/`check_types.py` (BE-1/2/5); services layer (BE-4); frontend feature folders + hooks + CSS modules (FE-2/4/5) — done together with the v2 design reskin and a11y fixes (FEX-1…FEX-9), behind a frontend test harness; OpenAPI→TS contract test (OPS-15/FE-3).

**Cross-cutting standards** (apply to every change, per epic #66): analyst state is sacred; WCAG AA + keyboard; reuse role gates; index + deterministic sort on `exception_records`; honest UTC labels; capped + CSV-injection-neutral exports + PII redaction on every egress; additive/versioned contracts; graceful degradation.

---

## 16. Appendix: important files

**Backend** — `app/main.py` (factory, CORS, mounting, `/health`, `/metrics`), `app/config.py` (settings/secrets/caps), `app/security.py` (JWT/bcrypt), `app/db.py` (engine, pool, `init_db`/migrations), `app/api/` (routers; many hold business/query logic), `app/core/` (profiler, check_types, runner, scheduler, ml, scorecards, incidents, sla), `app/connectors/` (sa, dialects, safety), `app/llm/` (providers, client, chat_agent, rca_agent, explorer, prompts), `app/observability.py`, `models.py`, `schemas.py`.

**Frontend** — `src/App.tsx` (routes/eager imports), `src/api/{client,types}.ts`, `src/lib/{format,prefs,useChatSocket,useThemeMode}.ts`, `src/components/{ui,Layout,ErrorBoundary,Modal,Markdown}.tsx`, `src/pages/` + `src/pages/dataset/`, `src/styles.css`.

**Infra/CI** — `.github/workflows/ci.yml`, `backend/Dockerfile`, `frontend/Dockerfile` + `nginx.conf`, `docker-compose.yml`, `monitoring/` (Prometheus/Grafana/Loki), `backend/migrations/`, `scripts/e2e_smoke.py`.

**Existing review docs** — `docs/BROKEN-FLOWS.md`, `docs/UI-GLOSSARY.md`, `docs/ui-flow-glossary.md`, `docs/competitive-analysis.md`, `docs/wireframes/`.

---

## 17. Final recommendation

Treat this as a hardening program with four parallel tracks, gated by Tier 0:

0. **Unblock production (Tier 0):** config fail-fast, analyst-state integrity, graceful shutdown, app-DB timeouts, object-level authz. File and fix before exposure.
1. **Scale the runtime:** source-query budgets, metadata indexes/aggregates, durable worker backpressure + retention.
2. **Harden operations:** timeouts, retries/circuit-breakers, readiness probes, image + supply-chain hardening, observability coverage, ops docs.
3. **Clarify the architecture & secure the surface:** split large modules, remove inverted deps, SSRF/headers/authz/LLM-risk, and the frontend a11y/structure refresh.

The single most important sequencing rule remains: **fix measurement, backpressure, and the Tier-0 blockers before increasing check volume or exposing the system.** Everything else can proceed incrementally.
