# DQ Sentinel Codebase Deep Dive and Implementation Action Plan

Last reviewed: 2026-06-19

Branch reviewed: `main...origin/main`

Working tree note: at review time, unrelated local changes already existed in
`frontend/src/components/DocsLauncher.tsx` and `frontend/src/styles.css`. This
document was added separately and does not depend on those changes.

## Purpose

This document consolidates the codebase review findings so the implementation
team has a single, detailed source for follow-up work. It covers:

- Efficiency, performance, and scalability for millions of source transactions
  per day on limited hardware.
- Backend structure, module boundaries, and API/service layering.
- Frontend structure, routing, API contract handling, and UI maintainability.
- Previously identified UI/product flow gaps that should remain on the
  implementation radar.
- Coding principles that are currently weak or inconsistently followed.
- A practical remediation roadmap.

This is a read-only review artifact. It does not imply all items should be fixed
in one change. Several findings need staged migrations, benchmarking, or product
decisions before implementation.

## Severity Model

| Tier | Meaning | Typical action |
|---|---|---|
| P0 | Can cause incorrect data, data loss, severe production instability, or a core workflow break. | Fix before scale-up or production hardening. |
| P1 | Likely to hurt scalability, maintainability, or operator trust as usage grows. | Plan into near-term engineering work. |
| P2 | Important quality, ergonomics, or technical-debt item. | Fix opportunistically or when touching the area. |
| P3 | Polish, cleanup, or future-proofing. | Batch with related work. |

## Executive Summary

DQ Sentinel has a solid early architecture: FastAPI, SQLAlchemy, explicit source
connectors, a read-only SQL guard, a worker process, TanStack Query on the
frontend, and a meaningful backend test suite. The product already has many
enterprise-grade concepts: profiling, generated checks, exception triage,
lineage, RCA, notification rules, scorecards, dashboards, audit logs, and
observability.

The main issue is that the codebase grew feature-first. Many features work, but
their implementation patterns are not yet ready for a high-volume deployment on
limited hardware. The biggest risks are:

1. Repeated full-source scans for profiling and checks.
2. Python/pandas materialization in hot paths.
3. Worker scheduling without durable backpressure.
4. Metadata history queries that load large row sets into Python.
5. Missing indexes for high-volume query patterns.
6. Large central modules with mixed responsibilities.
7. Frontend pages that combine fetching, mutation, state, layout, and business
   behavior in large files.
8. Manual backend/frontend schema mirroring that is already showing drift risk.

The recommended direction is incremental hardening, not a rewrite. Prioritize
source-query cost, metadata query cost, scheduler backpressure, and module
boundaries around the highest-change domains.

## System Shape Reviewed

The application is not a transaction ingestion system. It does not process every
source transaction as an event. It monitors source systems by querying them,
stores metadata in an app database, and presents the results in a web UI.

At "millions of transactions per day" scale, the stress points are therefore:

- Source database scans and aggregations.
- Frequency and cost of scheduled checks.
- Retained `check_runs`, `exception_records`, events, audit, and RCA/chat rows.
- Dashboard and insight queries over retained metadata history.
- Worker concurrency and resource contention.
- Browser rendering and bundle size for operator workflows.

This means the system can support high-volume source tables if it runs carefully
bounded checks against well-indexed source columns. It is not ready to run
frequent broad scans across many large, wide tables on small hardware without
the changes below.

---

# 1. Performance, Efficiency, and Scalability Findings

## PERF-1: Profiling Performs Many Full-Table Aggregations

Severity: P0 for large/wide datasets

Evidence:

- `backend/app/core/profiler.py:82`
- `backend/app/core/profiler.py:90`
- `backend/app/core/profiler.py:93`
- `backend/app/core/profiler.py:106`
- `backend/app/core/profiler.py:107`
- `backend/app/core/profiler.py:126`
- `backend/app/core/profiler.py:127`
- `backend/app/core/profiler.py:165`

Current behavior:

- `profile_dataset()` gets total row count using `SELECT COUNT(*)`.
- It fetches `SELECT *` into pandas up to `profile_sample_rows`, default 50,000.
- For each column, it runs exact `COUNT(column)` and `COUNT(DISTINCT column)`.
- For non-null columns, it tries exact `MIN(column)` and `MAX(column)`.
- For eligible columns, it runs exact top-values SQL with `GROUP BY`.

Why this hurts:

- Work scales with both row count and column count.
- A 100-column table can trigger hundreds of source queries.
- `COUNT(DISTINCT)` and top-value `GROUP BY` are often expensive on high-cardinality
  fields.
- Fetching `SELECT *` pulls wide rows when only a subset of columns may be needed.
- The app limits returned sample rows, but it does not cap the amount of work the
  source engine performs.

Implementation direction:

- Split profiling into "basic", "extended", and "expensive" tiers.
- Make exact distinct counts optional. Prefer approximate distinct where available:
  PostgreSQL extensions/cat stats where appropriate, BigQuery/Snowflake approximate
  functions, DuckDB approximate count distinct, or source-specific catalog stats.
- Avoid `SELECT *` for profiling. Project only columns that need local pandas
  sampling.
- Add a per-dataset profile budget:
  - max source queries
  - max elapsed seconds
  - max columns profiled in one pass
  - max local memory estimate
- Cache and reuse source row count for checks instead of recomputing constantly.
- Store profile freshness and quality metadata so the UI can tell the user whether
  a profile is sampled, exact, partial, or stale.

Validation:

- Add profiling tests that assert query count for a synthetic wide table.
- Add benchmarks for 10, 50, 100, and 300-column tables.
- Add metrics for profile source-query count, elapsed time, and sampled bytes.

## PERF-2: Common Check Execution Repeats Scans

Severity: P0 for frequent checks on large tables

Evidence:

- `backend/app/core/check_types.py:77`
- `backend/app/core/check_types.py:80`
- `backend/app/core/check_types.py:83`
- `backend/app/core/check_types.py:87`
- `backend/app/core/check_types.py:153`
- `backend/app/core/check_types.py:159`
- `backend/app/core/check_types.py:167`
- `backend/app/core/check_types.py:174`

Current behavior:

- `_sample_where()` runs:
  - a violation count query
  - a sample row query if violations exist
  - a total row count query
- Unique checks build duplicate groups, aggregate duplicate counts, sample
  duplicate rows, then count total rows.
- Many check types follow the same pattern: exact count first, sample second,
  total count third.

Why this hurts:

- Simple checks become two or three source queries each.
- For a table with many checks, the same table is scanned repeatedly.
- Total row count is often recomputed even though it changes slowly relative to
  the monitoring cadence.
- Query cost grows with number of checks, not just number of datasets.

Implementation direction:

- Treat row count as a cached dataset metric with a TTL or separate row-count
  check.
- For violation checks, consider returning:
  - exact violation count when cheap or required
  - capped sample and "at least N violations" when exact count is too expensive
- Add a check execution plan layer so related checks can share precomputed table
  facts when run in the same worker pass.
- Group checks by dataset/source and execute compatible aggregates together.
- For common column checks, generate a combined SQL query per table where possible.
  Example: one query can compute null counts for multiple columns.
- Add per-check cost hints and a "large table mode" that disables expensive exact
  counts by default.

Validation:

- Add tests for check result semantics when row count is cached/stale.
- Add source-query-count assertions for running multiple checks against one table.
- Add a benchmark for 10 checks on one million rows.

## PERF-3: Custom SQL Checks Execute Expensive SQL Twice

Severity: P1

Evidence:

- `backend/app/core/check_types.py:648`
- `backend/app/core/check_types.py:651`
- `backend/app/core/check_types.py:654`

Current behavior:

- The custom SQL is guarded.
- The runner wraps it as `SELECT COUNT(*) FROM (...)` to count violations.
- If count is non-zero, it runs the original SQL again with a sample limit.

Why this hurts:

- Arbitrary read-only SQL can still be expensive.
- Expensive joins and CTEs may run twice.
- The forced outer limit on the sample does not reduce the cost of the count.

Implementation direction:

- Add a custom SQL check mode:
  - `count_mode=exact`
  - `count_mode=sample_only`
  - `count_mode=exists_plus_sample`
- For limited hardware, default to sample-first plus an `EXISTS`/limited probe
  unless exact counts are explicitly required.
- Store `violation_count_exact=false` when counts are approximate or omitted.
- In the UI, label approximate counts clearly.

Validation:

- Unit test custom SQL modes.
- Integration test that custom SQL is not executed twice in sample-only mode.

## PERF-4: ML and Drift Checks Use Bounded But Heavy DataFrames

Severity: P1, P0 on small hardware when concurrent

Evidence:

- `backend/app/config.py:83`
- `backend/app/config.py:86`
- `backend/app/core/check_types.py:665`
- `backend/app/core/check_types.py:671`
- `backend/app/core/check_types.py:924`
- `backend/app/core/check_types.py:931`
- `backend/app/core/ml.py:52`
- `backend/app/core/ml.py:53`

Current behavior:

- `ml_max_rows` defaults to 50,000.
- ML outlier checks fetch `SELECT *` up to `max_rows`.
- Drift checks fetch a column up to `max_rows`.
- IsolationForest uses `n_estimators=200` and `n_jobs=-1`.
- Worker concurrency defaults to 4.

Why this hurts:

- Four concurrent workers can hold several 50k-row DataFrames at once.
- `SELECT *` for ML can pull wide tables into memory.
- `n_jobs=-1` means each ML check may use all cores, so multiple concurrent ML
  checks can oversubscribe CPU.
- Memory and CPU use are bounded by row caps, but the caps are too high for
  constrained boxes unless tuned.

Implementation direction:

- Add separate settings:
  - `DQ_ML_MAX_ROWS`
  - `DQ_ML_N_JOBS`
  - `DQ_ML_MAX_CONCURRENT_CHECKS`
  - `DQ_PROFILE_MAX_ROWS`
- Default `n_jobs` to 1 or a small configurable value.
- Do not fetch `SELECT *` for ML unless all numeric columns are needed. Project
  selected columns or discovered numeric columns only.
- Consider reservoir sampling or source-side random sampling where supported.
- Make ML checks opt-in for large tables.

Validation:

- Add tests that ML passes projected columns to the connector.
- Add a stress test that runs concurrent ML checks with memory monitoring.

## PERF-5: Scheduler Has No Durable Backpressure

Severity: P0 for production scale

Evidence:

- `backend/app/core/scheduler.py:104`
- `backend/app/core/scheduler.py:135`
- `backend/app/core/scheduler.py:139`
- `backend/app/core/scheduler.py:143`
- `backend/app/core/scheduler.py:149`
- `backend/app/core/scheduler.py:153`
- `backend/app/core/scheduler.py:165`

Current behavior:

- Every poll, scheduler does inline maintenance work.
- It claims up to 20 due checks.
- It advances `next_run_at` before execution.
- It submits work to a process-local `ThreadPoolExecutor`.
- There is no durable job table, queue depth, lease timeout, per-source limit, or
  backlog policy.

Why this hurts:

- If checks take longer than the poll interval, queued futures can grow.
- If a worker process dies after advancing `next_run_at`, work may be skipped until
  the next schedule.
- A single source can be overloaded by many simultaneous checks.
- Maintenance work competes with scheduling and execution inside the same loop.

Implementation direction:

- Introduce a `check_jobs` table:
  - `check_id`
  - `scheduled_for`
  - `status`
  - `claimed_by`
  - `claimed_until`
  - `attempt_count`
  - `last_error`
- Claim with row-level locking on Postgres.
- Add stale lease recovery.
- Add per-connection and per-dataset concurrency limits.
- Add policies for backlog:
  - skip stale interval jobs
  - coalesce multiple missed runs
  - always run the latest due check
- Move maintenance tasks to their own scheduled jobs.

Validation:

- Test crash/retry behavior with claimed jobs.
- Test that two workers do not run the same job.
- Test per-source concurrency limit.
- Add worker metrics for queue depth, age of oldest due job, in-flight jobs, and
  skipped/coalesced jobs.

## PERF-6: App Metadata Indexes Do Not Match High-Volume Queries

Severity: P1

Evidence:

- `backend/app/models.py:245`
- `backend/app/models.py:270`
- `backend/app/models.py:358`
- `backend/migrations/versions/0001_baseline.py`

Current behavior:

- `checks` has `ix_checks_due(status, next_run_at)`.
- `check_runs` has `ix_runs_check_started(check_id, started_at)` and dataset index.
- `exception_records` has useful indexes, but not enough for common trend and
  queue filters.

Missing or weak access patterns:

- `CheckRun.started_at >= cutoff GROUP BY status`
- `CheckRun.dataset_id + started_at desc`
- `CheckRun.status + started_at`
- latest run per check
- `ExceptionRecord.status + first_seen_at`
- `ExceptionRecord.dataset_id + first_seen_at`
- `ExceptionRecord.check_id + status`
- `ExceptionRecord.status + marked_at`
- `ExceptionRecord.last_run_id`
- text search across exception reason/note/check name

Implementation direction:

- Add an Alembic migration for composite indexes based on actual query plans.
- Candidate indexes:
  - `check_runs(started_at, status)`
  - `check_runs(dataset_id, started_at desc)`
  - `check_runs(check_id, started_at desc, id desc)`
  - `check_runs(status, started_at)`
  - `exception_records(status, first_seen_at)`
  - `exception_records(dataset_id, first_seen_at)`
  - `exception_records(check_id, status)`
  - `exception_records(status, marked_at)`
  - `exception_records(last_run_id)`
- For Postgres, consider partial indexes for open exceptions:
  - `where status = 'open'`
- Consider full-text/trigram search for exception text search.

Validation:

- Capture `EXPLAIN ANALYZE` for dashboard, exceptions list, runs list, insights,
  and scorecards before/after.
- Add migration drift tests, per AGENTS.md.

## PERF-7: Dashboard, Insights, and Scorecards Load Large History Into Python

Severity: P1

Evidence:

- `backend/app/api/dashboard.py:47`
- `backend/app/api/dashboard.py:48`
- `backend/app/api/dashboard.py:52`
- `backend/app/api/insights.py:96`
- `backend/app/api/insights.py:168`
- `backend/app/core/scorecards.py:361`
- `backend/app/core/scorecards.py:374`
- `backend/app/core/scorecards.py:378`
- `backend/app/core/scorecards.py:649`
- `backend/app/core/scorecards.py:653`

Current behavior:

- Dashboard trend loads all 14-day `CheckRun` rows and buckets in Python.
- Check matrix loads all selected check runs in a date window and buckets in Python.
- Exception series loads every matching exception timestamp and buckets in Python.
- Scorecards load all historical runs for selected checks to derive latest status.
- Scorecard backfill loads all run timestamps in a window and repeatedly captures
  snapshots.

Why this hurts:

- Query result size grows linearly with retained history.
- CPU and memory shift from the database to the API process.
- Limited hardware pays for both DB read and Python bucketing.
- "Latest per check" should not require loading all historical rows.

Implementation direction:

- Move bucketing into SQL:
  - `GROUP BY date_trunc('day', started_at), status`
  - SQLite-compatible equivalent for tests/dev
- Use window functions or subqueries for latest run per check.
- For live scorecards, use `Check.last_status` where semantics allow it.
- Persist daily rollups and query those for long windows.
- Restrict backfill to batched days and use aggregate queries.

Validation:

- Add tests that SQL aggregation returns the same response shape.
- Add load tests with 1M `check_runs` rows and 1M `exception_records` rows.

## PERF-8: Offset Pagination and Per-Row Serialization Will Degrade

Severity: P1

Evidence:

- `backend/app/api/runs.py:29`
- `backend/app/api/runs.py:60`
- `backend/app/api/runs.py:61`
- `backend/app/api/serialize.py:32`
- `backend/app/api/serialize.py:39`
- `backend/app/api/exceptions_api.py:135`
- `backend/app/api/exceptions_api.py:145`
- `backend/app/api/exceptions_api.py:147`
- `backend/app/api/serialize.py:54`
- `backend/app/api/serialize.py:56`
- `backend/app/api/serialize.py:62`

Current behavior:

- Runs and exceptions use `count()` plus offset pagination.
- `run_out()` does a per-run exception count.
- `exception_out()` does per-row lookup of check, dataset, and users.

Why this hurts:

- Offset gets slower at high offsets.
- Exact count can dominate page load on large filtered sets.
- Per-row lookups create N+1 query patterns.

Implementation direction:

- Switch high-volume lists to cursor pagination.
- Return `has_more` instead of exact `total` where exact total is not essential.
- For exact totals, compute asynchronously or cache them by filter when needed.
- Bulk-load related checks/datasets/users for page rows.
- Add serializer functions that accept preloaded maps.

Validation:

- Add query-count tests for runs and exceptions list pages.
- Add tests for cursor stability under new inserts.

## PERF-9: Exception Auto-Resolve Can Load and Write Too Much at Once

Severity: P1

Evidence:

- `backend/app/core/runner.py:141`
- `backend/app/core/runner.py:151`
- `backend/app/core/runner.py:155`
- `backend/app/core/runner.py:160`
- `backend/app/core/runner.py:171`

Current behavior:

- When a check passes, `_auto_resolve_passing()` loads all open exception IDs for
  that check into Python.
- It bulk-updates the exception records.
- It then inserts one `ExceptionEvent` per exception.

Why this hurts:

- If a check has many open exceptions, a passing run can create a large memory
  spike and a huge transaction.
- Event rows multiply the write amplification of auto-resolution.

Implementation direction:

- Process auto-resolve in batches.
- Add a summarized system event option for very large batches.
- Consider a separate background job for event materialization.
- Add a hard cap and surface "auto-resolve partially completed" if needed.

Validation:

- Test auto-resolve with 100k open exceptions in batches.
- Confirm audit/event semantics remain acceptable for compliance needs.

## PERF-10: Source Result Materialization Uses `fetchall()`

Severity: P2, P1 for wide rows or many concurrent requests

Evidence:

- `backend/app/connectors/sa.py:201`
- `backend/app/connectors/sa.py:213`
- `backend/app/connectors/sa.py:230`
- `backend/app/connectors/sa.py:236`

Current behavior:

- `run_select()` executes SQL and calls `fetchall()`.
- `fetch_df()` uses pandas `read_sql`.
- Result row caps exist, but memory is still all-at-once for the capped result.

Why this hurts:

- Capped results can still be wide and memory-heavy.
- Concurrent requests multiply memory usage.
- For exports or future larger result sets, the pattern does not stream.

Implementation direction:

- Add streaming/chunked fetch helpers.
- Project fewer columns for sample and ML paths.
- Track row width or serialized payload size.
- Add response-size limits, not just row-count limits.

Validation:

- Add tests for payload cap behavior.
- Add memory profiling for wide-row result sets.

## PERF-11: Source Introspection and Health Checks Can Become Load Generators

Severity: P2

Evidence:

- `backend/app/connectors/sa.py:116`
- `backend/app/connectors/sa.py:138`
- `backend/app/api/connections.py:19`
- `backend/app/api/connections.py:40`

Current behavior:

- `list_tables()` enumerates visible schemas, tables, and views.
- `schema_tree()` calls `get_columns()` per object.
- Fleet health probes all connections concurrently, up to 8 at a time.

Why this hurts:

- Large warehouses can have thousands of objects.
- Schema browsing can produce many metadata queries.
- Health endpoints can hit every source at once.

Implementation direction:

- Cache schema trees per connection with TTL and explicit refresh.
- Add pagination/search to source browse endpoints.
- Add a max object cap with a clear "narrow your search" response.
- Make fleet health asynchronous or cached.
- Add per-source health probe timeout.

Validation:

- Test source browse with thousands of synthetic objects.
- Add metrics for schema introspection time and object counts.

## PERF-12: Compose Deployment Has No Resource Budget

Severity: P2

Evidence:

- `docker-compose.yml:7`
- `docker-compose.yml:23`
- `docker-compose.yml:48`
- `docker-compose.yml:59`
- `docker-compose.yml:67`

Current behavior:

- Compose uses Postgres, API, worker, frontend, Prometheus, Loki, Promtail, and
  Grafana.
- It has restart policies and health checks.
- It does not define CPU/memory reservations or limits.
- It does not define worker scaling or resource-specific settings.

Why this hurts:

- On limited hardware, observability services can compete with the app.
- Worker, API, and ML jobs can overrun memory/CPU.
- There is no documented small-hardware profile.

Implementation direction:

- Add a "small hardware" deployment profile:
  - worker concurrency 1 or 2
  - ML disabled or constrained
  - lower profile/ML row caps
  - lower source pool sizes
  - optional observability stack
- Add resource guidance in docs.
- Consider separate compose override files:
  - `docker-compose.small.yml`
  - `docker-compose.observability.yml`
  - `docker-compose.prod.yml`

Validation:

- Smoke test on a constrained VM profile.
- Track memory and CPU under sample workload.

---

# 2. Backend Structure and Maintainability Findings

## BE-1: `schemas.py` Is a Cross-Domain Contract Monolith

Severity: P1

Evidence:

- `backend/app/schemas.py`
- Over 1,100 lines.
- Contains auth, connections, datasets, profiles, scorecards, SLA, knowledge,
  checks, monitor packs, contracts, runs, incidents, exceptions, RCA, chat,
  query/workbench, saved queries, notifications, audit, dashboards, lineage,
  search, docs, widget policy constants, and insight responses.

Why this hurts:

- Unrelated teams/features edit the same file.
- Merge conflicts become more likely.
- It is hard to identify ownership.
- Frontend type mirroring becomes more error-prone.
- Domain constants like widget caps are mixed with generic API schemas.

Implementation direction:

- Split schemas by domain:
  - `app/schemas/auth.py`
  - `app/schemas/datasets.py`
  - `app/schemas/checks.py`
  - `app/schemas/exceptions.py`
  - `app/schemas/dashboards.py`
  - `app/schemas/scorecards.py`
  - etc.
- Keep a compatibility barrel in `app/schemas/__init__.py` if broad imports need
  a staged migration.
- Move quota/policy constants to domain modules, for example
  `app/core/dashboard_policy.py`.

Validation:

- Keep OpenAPI output stable unless intentionally changing contracts.
- Run backend tests and frontend build after each split.

## BE-2: `models.py` Is a Growing Metadata Model Monolith

Severity: P2 now, P1 later

Evidence:

- `backend/app/models.py`
- Over 500 lines.
- Contains all ORM models across auth, connections, datasets, profiles, checks,
  runs, exceptions, incidents, dashboards, chat, saved queries, audit, SLA, RCA,
  contracts, and notifications.

Why this hurts:

- Model ownership is unclear.
- Large schema changes become harder to review.
- Domain-level migrations are harder to reason about.

Implementation direction:

- Keep a central SQLAlchemy `Base`, but split model classes by domain:
  - `app/models/base.py`
  - `app/models/checks.py`
  - `app/models/exceptions.py`
  - `app/models/datasets.py`
  - etc.
- Keep `app/models/__init__.py` exporting all models for compatibility.
- Do this carefully with Alembic autogenerate tests.

Validation:

- `tests/test_migrations.py` must remain green.
- Confirm Alembic metadata still sees all models.

## BE-3: Core Depends on API Serialization

Severity: P1

Evidence:

- `backend/app/core/contracts.py:17` imports `from app.api.serialize import check_out`.

Why this hurts:

- Core domain logic should not depend on HTTP/API presentation.
- It creates an inverted dependency direction.
- It makes core harder to reuse from workers or scripts.
- It can create import cycles as APIs grow.

Implementation direction:

- Move shared projection logic out of `api.serialize` into a neutral module:
  - `app/core/projections.py`
  - or `app/services/serialization.py`
- Keep API-specific response assembly in API layer.
- If contracts need only a small check summary, return a domain dict or domain DTO
  from core and let API convert it to Pydantic output.

Validation:

- Add an import-boundary test if desired:
  - `core` should not import `app.api`.

## BE-4: API Routers Contain Too Much Business Logic

Severity: P1

Evidence examples:

- `backend/app/api/dashboard.py`
- `backend/app/api/exceptions_api.py`
- `backend/app/api/datasets.py`
- `backend/app/api/custom_dashboards.py`
- `backend/app/api/connections.py`
- `backend/app/api/query.py`

Current behavior:

- Routers directly build complex queries.
- Routers run source connector work.
- Routers perform export logic.
- Routers contain policy and transformation logic.

Why this hurts:

- Harder to unit test without HTTP.
- More duplicate query/invalidation behavior.
- Harder to optimize shared behavior.
- API files become mixed HTTP + domain service + repository layers.

Implementation direction:

- Adopt a lightweight service layer, not a heavy framework:
  - router: auth, input, response, status codes
  - service: business behavior and transaction boundaries
  - repository/query module: reusable DB query shapes
- Start with high-churn areas:
  - exceptions
  - dashboard/insights
  - checks/runs
  - datasets/profiling

Validation:

- Add service-level unit tests for query/filter behavior.
- Keep endpoint tests for auth and response shape.

## BE-5: `check_types.py` Is a God Module

Severity: P1

Evidence:

- `backend/app/core/check_types.py`
- Over 1,100 lines.
- Contains parameter schema metadata, validation, SQL generation, Python regex
  fallback, row count anomaly, freshness, custom SQL, ML outlier, distribution
  drift, schema change, and the registry.

Why this hurts:

- Adding a check type touches a huge file.
- Testing individual families is less focused.
- Performance changes can accidentally affect unrelated checks.
- Domain-specific dependencies like pandas/scipy/sklearn sit near simple SQL
  checks.

Implementation direction:

- Split by family:
  - `core/checks/base.py`
  - `core/checks/registry.py`
  - `core/checks/simple_sql.py`
  - `core/checks/freshness.py`
  - `core/checks/volume.py`
  - `core/checks/custom_sql.py`
  - `core/checks/ml.py`
  - `core/checks/drift.py`
  - `core/checks/schema.py`
- Keep `CHECK_TYPES`, `validate_check`, and `run_check_type` public from a
  compatibility module.
- Move shared helpers like `_sample_where` into a small execution utility module.

Validation:

- `backend/tests/test_checks.py` should pass unchanged initially.
- Add targeted tests for each check family.

## BE-6: Router Mounting Is Slightly Irregular

Severity: P3

Evidence:

- `backend/app/main.py:41`
- `backend/app/main.py:42`
- `backend/app/api/lineage.py:1`
- `backend/app/api/lineage.py:16`

Current behavior:

- Most routers mount through `api_router`.
- Lineage mounts separately because it spans dataset and connection paths and has
  no router prefix.

Why this hurts:

- Discoverability is weaker.
- Future route collisions are easier.

Implementation direction:

- Include lineage in `api/__init__.py` like the other routers.
- Keep explicit full paths if needed, but mount in one place.
- Alternatively split lineage into `dataset_lineage.py` and `connection_lineage.py`.

Validation:

- Confirm OpenAPI route list remains identical.

## BE-7: Source Connector Layer Is Good But Needs Capacity Controls

Severity: P1 for scale

Evidence:

- `backend/app/connectors/sa.py`
- `backend/app/connectors/dialects.py`
- `backend/app/connectors/safety.py`

What is good:

- Source SQL goes through `guard_sql()`.
- Engines open read-only where supported.
- Source engines are cached per connection.
- Dialect support is centralized.
- PostgreSQL/MySQL source pools have explicit pool sizes.

Remaining gap:

- Source pools and worker concurrency are independent.
- There is no app-level per-source concurrency cap.
- Some dialects rely on default SQLAlchemy pool behavior.
- Statement timeout is only explicit for PostgreSQL in the current dialect options.

Implementation direction:

- Add per-source concurrency semaphores keyed by connection ID.
- Add dialect-specific statement/query timeout options where possible.
- Add source pool settings to app config.
- Track source query queueing time separately from execution time.

Validation:

- Test that two checks on one connection honor the cap.
- Add metrics by connection kind, not raw connection name, to avoid high cardinality.

---

# 3. Frontend Structure and Maintainability Findings

## FE-1: Routes Are Eagerly Imported

Severity: P2 for performance, P1 as app grows

Evidence:

- `frontend/src/App.tsx:6`
- `frontend/src/App.tsx:28`

Current behavior:

- Every page is imported in `App.tsx`.
- Large pages like Workbench, Settings, Home, and Assistant are part of the initial
  app bundle.

Why this hurts:

- Slower startup on low-end clients.
- Large route modules load even when not used.
- Feature code is less independently owned.

Implementation direction:

- Use `React.lazy` and `Suspense` for route-level splitting.
- Keep Login and core shell eager.
- Lazy-load heavy routes:
  - Workbench
  - Settings
  - Assistant
  - Lineage
  - Custom dashboards
  - Dataset detail tabs if needed

Validation:

- Run `npm run build` and inspect chunk sizes.
- Use Playwright or browser smoke to confirm lazy routes load correctly.

## FE-2: Large Pages Own Too Many Responsibilities

Severity: P1

Evidence:

- `frontend/src/pages/WorkbenchPage.tsx`
- `frontend/src/pages/SettingsPage.tsx`
- `frontend/src/pages/HomePage.tsx`
- `frontend/src/pages/CustomDashboardPage.tsx`
- `frontend/src/pages/IncidentsPage.tsx`

Current behavior:

- Pages combine:
  - route params
  - data fetching
  - mutations
  - query invalidation
  - local UI state
  - forms
  - table rendering
  - layout
  - business rules

Why this hurts:

- Hard to test page behavior.
- Hard to reuse smaller workflows.
- Small changes risk breaking unrelated UI within the page.
- Files become difficult to review.

Implementation direction:

- Split pages into feature folders:
  - `features/workbench/`
  - `features/settings/`
  - `features/home/`
  - `features/incidents/`
- For each feature, create:
  - `api.ts` or `hooks.ts`
  - `types.ts` if feature-local
  - presentational components
  - route component
- Extract forms from `SettingsPage`:
  - `AuditLogPanel`
  - `McpServersPanel`
  - `NotificationRulesPanel`
  - `UsersPanel`
- Extract workbench internals:
  - `SchemaBrowser`
  - `SavedQueriesPanel`
  - `QueryHistoryDrawer`
  - `WorkbenchToolbar`
  - `ResultPanel`

Validation:

- Add component tests for extracted form components.
- Keep route-level smoke tests.

## FE-3: API Types Are Manually Mirrored and Already Duplicated

Severity: P1

Evidence:

- `frontend/src/api/types.ts`
- `frontend/src/api/types.ts:439`
- `frontend/src/api/types.ts:965`

Current behavior:

- Backend Pydantic schemas are mirrored manually in TypeScript.
- `ExceptionPage` is declared twice.
- TypeScript declaration merging makes the duplicate legal today because the
  shapes match.

Why this hurts:

- If one copy changes later, TypeScript may merge incompatible intent instead of
  catching a clean duplicate-name error.
- Backend/frontend contract drift is likely as schemas grow.
- One large file repeats the backend `schemas.py` monolith problem.

Implementation direction:

- Remove the duplicate `ExceptionPage`.
- Split `api/types.ts` by domain.
- Longer term: generate TypeScript types from OpenAPI.
  - Use the FastAPI OpenAPI output.
  - Generate a typed client or at least generated schemas.
  - Keep handwritten convenience hooks on top.

Validation:

- `npm run typecheck`.
- Add CI check that generated types are up to date if generation is adopted.

## FE-4: API Calls Are Consistent But Not Encapsulated by Feature Hooks

Severity: P2

Evidence:

- `frontend/src/api/client.ts`
- Many `useQuery()` and `useMutation()` calls directly in pages/components.

What is good:

- Components use `api` wrapper instead of raw `fetch`.
- TanStack Query is used consistently for server state.

Remaining gap:

- Query keys and endpoint strings are repeated.
- Invalidations are scattered.
- Feature behavior is harder to refactor.

Implementation direction:

- Add feature hooks:
  - `useDatasets()`
  - `useDataset(id)`
  - `useRuns(params)`
  - `useExceptions(filters)`
  - `useCheckTypes()`
  - `useConnectionHealth()`
- Centralize query keys:
  - `queryKeys.datasets.list(params)`
  - `queryKeys.runs.list(params)`
  - etc.
- Put mutation invalidations next to the mutation hook.

Validation:

- Typecheck.
- Add tests for hooks with mocked query client where high-value.

## FE-5: Styling Is Centralized and Inline Styles Are Common

Severity: P2

Evidence:

- `frontend/src/styles.css`
- Many `style={{ ... }}` usages in page/component files.

Current behavior:

- One global CSS file contains most styles.
- Components use many inline style overrides.

Why this hurts:

- Hard to find ownership of a visual rule.
- Inline styles make responsive fixes repetitive.
- Visual drift becomes likely as screens multiply.

Implementation direction:

- Do not add a UI kit casually; existing guidance says no UI kit dependency.
- Split CSS by feature or by component category:
  - `styles/base.css`
  - `styles/layout.css`
  - `styles/tables.css`
  - `styles/forms.css`
  - `features/workbench/workbench.css`
- Gradually replace repeated inline styles with utility classes or component
  props.
- Keep design tokens in one place.

Validation:

- Visual smoke via Playwright screenshots for core screens.
- Manual check on mobile and desktop breakpoints.

## FE-6: Frontend Lacks Test Coverage

Severity: P1 for long-term product stability

Evidence:

- No `*.test.*` or `*.spec.*` files found under `frontend/src`.
- Backend has broad pytest coverage under `backend/tests`.

Why this hurts:

- Complex UI workflows rely on manual validation.
- Refactors of large pages are riskier.
- Query/invalidation behavior is not protected.

Implementation direction:

- Add Vitest + React Testing Library if dependency policy allows.
- Start with high-risk, low-flake tests:
  - `api/client` error handling
  - `CheckParamsForm`
  - exception filter URL behavior
  - workbench tab persistence helpers
  - dashboard widget config validation
- Add Playwright smoke tests for:
  - login
  - register/profile/generate checks
  - run check
  - triage exception
  - workbench query

Validation:

- CI should run frontend unit tests and build.

---

# 4. UI and Product Flow Findings to Keep on the Roadmap

The repo already contains detailed UI flow review artifacts:

- `docs/BROKEN-FLOWS.md`
- `docs/UI-GLOSSARY.md`
- `docs/ui-flow-glossary.md`

The items below are the highest-impact flow issues from those reviews that remain
important to implementation planning. Verify current code before implementing,
because some may have been partially fixed since the docs were written.

## UI-1: Deep Screens Need Stronger Breadcrumbs and Context Return

Severity: P1

Affected workflows:

- Dataset detail entered from search, lineage, runs, exceptions, assistant, or
  notifications.
- Workbench opened from a dataset/run/exception.
- RCA reached from runs or datasets.
- Filtered exceptions pages.

Why this matters:

- Analysts move through investigation paths, not isolated pages.
- Losing origin increases cognitive load and makes backtracking harder.

Implementation direction:

- Add compact breadcrumbs to deep screens.
- Preserve origin query params where useful.
- Standardize "Back to run", "Back to dataset", and "Open in workbench" behavior.

## UI-2: Destructive Actions Need Consistent Confirmation or Undo

Severity: P1

Affected actions from prior UI review:

- Archive/dismiss checks.
- Delete dashboards.
- Delete chat sessions.
- Delete MCP servers.
- Deactivate users.
- Change roles.
- Delete connections with cascading dependent data.

Implementation direction:

- Route destructive actions through app `Modal`, not native `window.confirm`.
- Require typed confirmation for cascading deletes.
- Add undo for low-risk soft deletes where cheap.
- Avoid mutation on select change for role changes; require an explicit Save.

## UI-3: Unsaved Work Guards Need to Be Consistent

Severity: P1

Affected workflows:

- Knowledge tab.
- Create/edit modals.
- Dashboard builder.
- Workbench tabs and SQL edits.

Implementation direction:

- Add dirty-state primitives:
  - `useDirtyGuard`
  - modal close guard
  - route/tab navigation guard where applicable
- Autosave drafts for high-effort text areas where appropriate.

## UI-4: Exceptions Filter State Must Stay Transparent

Severity: P1

Known prior issue:

- Changing dataset filter while scoped by run could silently drop `run_id`.

Implementation direction:

- Preserve unrelated query params unless the user explicitly clears them.
- Show active filters as removable chips.
- Add tests for URL filter transitions.

## UI-5: Disabled Controls Need Recovery Instructions

Severity: P2

Examples:

- Generate checks/dashboard disabled until profile exists.
- Assistant/RCA disabled without LLM config or editor role.

Implementation direction:

- Disabled states should explain what unlocks the action.
- Provide the next action nearby: "Profile now", "Ask admin", "Configure LLM".

---

# 5. Security, Privacy, and Operational Notes

## SEC-1: Source SQL Safety Is a Strong Foundation

Severity: positive finding

Evidence:

- `backend/app/connectors/safety.py`
- `backend/app/connectors/sa.py`
- AGENTS.md golden rule: all source SQL must pass `guard_sql()`.

What is good:

- Source SQL is guarded as single SELECT/CTE.
- Mutating SQL is blocked.
- Limits are enforced on returned rows.
- LLM-authored SQL flows through the same guarded path.

Remaining gap:

- Guarding returned rows is not the same as limiting source work.
- Expensive read-only queries are still possible.

Implementation direction:

- Add statement timeouts for more dialects.
- Add source query budgets and cancellation support where possible.
- Add source-side query labels/comments where supported for observability.

## SEC-2: DSNs Are Stored Plaintext

Severity: P1 for production hardening

Evidence:

- `backend/app/models.py:51` comments that DSN is plaintext today.
- Existing docs also note secrets-at-rest as a gap.

Implementation direction:

- Encrypt DSNs at rest.
- Prefer external secret storage for production.
- Separate display/masked DSN from stored secret.
- Add key rotation story.

Validation:

- Tests must ensure audit logs and API responses never expose credentials.
- Confirm backups do not expose plaintext DSNs after migration.

## SEC-3: JWT in localStorage Has Tradeoffs

Severity: P2, threat-model dependent

Evidence:

- `frontend/src/api/client.ts:5`
- `frontend/src/api/client.ts:15`
- `frontend/src/api/client.ts:19`

Current behavior:

- JWT is stored in localStorage.

Why this matters:

- Simpler implementation.
- More exposed to XSS than HttpOnly cookies.

Implementation direction:

- If production threat model requires it, move to secure HttpOnly cookies with CSRF
  protection.
- At minimum, keep access token lifetime limited and harden CSP.

## SEC-4: LLM Privacy Guardrails Exist and Should Be Preserved

Severity: positive finding, P1 if changed carelessly

Evidence:

- AGENTS.md LLM integration notes.
- `backend/app/core/profiler.py` profile summarization.
- `backend/app/llm/client.py` redaction/error handling paths.

What is good:

- LLM features degrade when no key is configured.
- Prompts should include aggregates and limited sample rows.
- PII columns are redacted.
- User-facing LLM errors go through safe messages.

Implementation direction:

- Add tests around prompt sample-row limits and PII redaction if not already
  comprehensive.
- Keep provider-specific SDK use inside providers.

---

# 6. Testing and Verification Gaps

## TEST-1: Backend Test Coverage Is Broad But Mostly SQLite-Centered

Severity: P2

Evidence:

- `backend/tests/`
- `backend/tests/conftest.py`
- Many tests use SQLite source/app DB.

What is good:

- There is broad pytest coverage across checks, API, lineage, exceptions, custom
  dashboards, notifications, scorecards, migrations, chat, contracts, and more.

Remaining gaps:

- Production app DB is Postgres in compose.
- Some performance and locking behavior differs between SQLite and Postgres.
- Source dialect behavior differs across engines.

Implementation direction:

- Keep SQLite tests for speed.
- Add a smaller Postgres integration suite in CI for:
  - migrations
  - worker claim concurrency
  - indexes/query plans
  - JSON behavior
  - row-level locking once durable jobs are added

## TEST-2: Frontend Has No Unit/Component Tests

Severity: P1

Evidence:

- No frontend test files found under `frontend/src`.

Implementation direction:

- Add frontend tests before major page decomposition.
- Start with pure helpers and high-risk components.
- Add a small Playwright smoke suite for core flows.

## TEST-3: Performance Tests Are Missing

Severity: P1 for scale goals

Implementation direction:

- Add a synthetic metadata load generator:
  - N datasets
  - M checks
  - millions of check runs
  - millions of exceptions
- Add source-table benchmarks:
  - wide table profiling
  - many checks on one large table
  - ML check memory/CPU
  - dashboard/insights response time
- Define performance budgets:
  - dashboard p95
  - exceptions list p95
  - run list p95
  - worker backlog max age
  - max memory per worker

---

# 7. Coding Principles Currently Weak or Inconsistently Applied

## Principle: Single Responsibility

Weak areas:

- `backend/app/core/check_types.py`
- `backend/app/schemas.py`
- `backend/app/models.py`
- large frontend pages
- `frontend/src/styles.css`

What to do:

- Split by domain and responsibility.
- Keep compatibility exports during migration.

## Principle: Separation of Concerns

Weak areas:

- API routers perform business logic and query construction.
- Core imports API serialization.
- Frontend pages combine data, state, forms, and layout.

What to do:

- Introduce services/repositories on backend.
- Introduce feature hooks and presentational components on frontend.

## Principle: Push Work to the Right Layer

Weak areas:

- Dashboard/insights/scorecards bucket large histories in Python.
- Profiling uses many exact source queries without cost tiers.

What to do:

- Push aggregation to app DB.
- Push only safe, budgeted work to source DB.
- Pull only bounded, projected data into Python.

## Principle: Explicit Resource Budgets

Weak areas:

- Worker queue has no durable backpressure.
- ML uses all cores by default.
- Source concurrency is not capped per connection.
- Compose has no small-hardware profile.

What to do:

- Add queue depth, leases, concurrency caps, and resource settings.
- Document recommended small/medium/large hardware settings.

## Principle: DRY API Contracts

Weak areas:

- Backend `schemas.py` and frontend `api/types.ts` are manually mirrored.
- Frontend has duplicate `ExceptionPage`.

What to do:

- Generate frontend types from OpenAPI or split and enforce contract sync.

## Principle: Test the Highest-Risk User Paths

Weak areas:

- No frontend tests.
- No performance tests.
- Limited production-like Postgres concurrency testing.

What to do:

- Add frontend unit/component tests.
- Add Playwright smoke tests.
- Add synthetic performance tests.

---

# 8. Recommended Implementation Roadmap

## Phase 0: Quick Wins and Safety Fixes

Goal: reduce immediate risk without large architecture changes.

Tasks:

1. Remove duplicate `ExceptionPage` in `frontend/src/api/types.ts`.
2. Lazy-load heavy frontend routes.
3. Add `DQ_ML_N_JOBS` and default ML `n_jobs` to 1.
4. Lower small-hardware defaults in a documented compose override.
5. Add missing obvious metadata indexes after checking query plans.
6. Move `core/contracts.py` away from importing `app.api.serialize`.
7. Add query-count tests for runs/exceptions serialization.
8. Add a simple frontend test setup and cover URL filter behavior.

## Phase 1: Metadata Query Scalability

Goal: make app DB history scale.

Tasks:

1. Rewrite dashboard trends as SQL aggregates.
2. Rewrite insight series/matrix bucketing as SQL aggregates.
3. Optimize scorecard latest-status lookup.
4. Bulk-load serializers for list pages.
5. Add cursor pagination for runs and exceptions.
6. Add retention settings and archival policy.
7. Add metadata load tests.

## Phase 2: Source Query Cost Control

Goal: prevent source DB scans from dominating runtime.

Tasks:

1. Add profile tiers and query budgets.
2. Replace exact distinct/top-values defaults with sampled/approx/cost-aware modes.
3. Cache row count and table facts.
4. Combine compatible checks by dataset.
5. Add custom SQL count modes.
6. Add per-source concurrency limits.
7. Add statement timeouts across dialects where supported.

## Phase 3: Worker and Deployment Hardening

Goal: make scheduled execution reliable on constrained hardware.

Tasks:

1. Introduce durable job table with leases.
2. Add worker backpressure and backlog coalescing.
3. Split maintenance jobs from check execution.
4. Add queue depth and oldest-job metrics.
5. Add small/medium/large deployment profiles.
6. Add Postgres concurrency tests.

## Phase 4: Structural Refactor

Goal: reduce ongoing cost of feature development.

Tasks:

1. Split backend schemas by domain.
2. Split backend models by domain with compatibility exports.
3. Split `check_types.py` by check family.
4. Move backend feature logic into services.
5. Split frontend large pages into feature folders.
6. Add feature API hooks and centralized query keys.
7. Split CSS into maintainable modules.

---

# 9. Suggested Ticket Breakdown

## Scalability Tickets

- Add metadata DB indexes for run and exception high-volume filters.
- Rewrite dashboard trend to SQL aggregate.
- Rewrite exception-series to SQL aggregate.
- Optimize scorecard latest statuses.
- Add cursor pagination for runs.
- Add cursor pagination for exceptions.
- Add profile tiers and disable expensive exact stats by default.
- Add check execution row-count cache.
- Add custom SQL count modes.
- Add source per-connection concurrency caps.
- Add ML CPU/concurrency settings.
- Add durable check job queue.
- Add retention/archive policy for runs, exceptions, events, audit, chat, RCA.

## Backend Structure Tickets

- Remove core dependency on API serializer.
- Split `schemas.py` by domain.
- Split `check_types.py` by check family.
- Extract exceptions service/repository.
- Extract dashboard/insights service queries.
- Consolidate lineage router mounting.
- Add import-boundary lint/test.

## Frontend Structure Tickets

- Remove duplicate `ExceptionPage`.
- Add route-level lazy loading.
- Add feature query hooks and query key factory.
- Split Workbench page into feature components.
- Split Settings page into panels.
- Split API types by domain or generate from OpenAPI.
- Add frontend unit test setup.
- Add Playwright smoke tests.
- Split global CSS into modules/sections.

## UI Flow Tickets

- Add breadcrumbs/context return to deep screens.
- Standardize destructive action confirmations.
- Add dirty-state guard for forms and route/tab switches.
- Preserve exceptions URL filters and show filter chips.
- Add recovery hints for disabled controls.

## Security/Ops Tickets

- Encrypt DSNs at rest or integrate secret storage.
- Evaluate localStorage JWT vs HttpOnly cookie model.
- Add source statement timeouts for more dialects.
- Add small-hardware compose override.
- Add production resource guidance.

---

# 10. Appendix: Important Files

Backend:

- `backend/app/main.py` - FastAPI app factory and router mounting.
- `backend/app/api/` - routers. Many contain business/query logic today.
- `backend/app/core/` - domain logic. Some modules are too broad.
- `backend/app/connectors/` - source DB access and SQL safety.
- `backend/app/llm/` - provider abstraction and agents.
- `backend/app/models.py` - all ORM metadata models.
- `backend/app/schemas.py` - all Pydantic API contracts.
- `backend/app/core/check_types.py` - check registry and execution.
- `backend/app/core/profiler.py` - dataset profiling.
- `backend/app/core/scheduler.py` - worker scheduling loop.
- `backend/app/core/runner.py` - check execution and exception reconciliation.
- `backend/app/core/scorecards.py` - scorecard scoring/snapshots.

Frontend:

- `frontend/src/App.tsx` - route definitions and eager page imports.
- `frontend/src/api/client.ts` - API wrapper.
- `frontend/src/api/types.ts` - manually mirrored API types.
- `frontend/src/pages/` - route-level pages.
- `frontend/src/pages/dataset/` - dataset detail tabs.
- `frontend/src/components/` - shared components.
- `frontend/src/components/exceptions/` - exception workspace components.
- `frontend/src/components/dashboards/` - custom dashboard widgets/config.
- `frontend/src/components/workbench/` - workbench editor/grid pieces.
- `frontend/src/styles.css` - global CSS.

Existing review docs:

- `docs/BROKEN-FLOWS.md`
- `docs/UI-GLOSSARY.md`
- `docs/ui-flow-glossary.md`
- `docs/competitive-analysis.md`

---

# 11. Final Recommendation

Treat this as a hardening program with three parallel tracks:

1. Scale the runtime: source query budgets, metadata indexes/aggregates, durable
   worker backpressure.
2. Clarify the architecture: split large backend/frontend modules along feature
   boundaries and remove inverted dependencies.
3. Protect workflows: add frontend tests, route-level lazy loading, dirty guards,
   and consistent destructive-action handling.

The most important sequencing rule: fix measurement and backpressure before
increasing check volume. Without query budgets, queue depth metrics, and metadata
query optimization, adding more checks or more frequent schedules will make the
system look functional in demos but unstable under real production load.
