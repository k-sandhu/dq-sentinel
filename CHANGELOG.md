# Changelog

All notable changes to DQ Sentinel are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/) with the usual pre-1.0 caveat that minor versions
may contain breaking changes.

## [Unreleased]

Two batches so far: the UX-benchmark fixes that shipped as PRs #276–#280, and
the remediation of the July 2026 end-to-end system audit
(`docs/system-audit-2026-07.md`, issues #281–#295, which also closed out the
older #72, #261 and #273). The audit remediation then went through two
adversarial review rounds; where a round found that a fix did not hold, the
entry below describes what actually shipped, not what was first attempted.

### Added

- **Bulk proposal triage.** A page leading with 100+ fully expanded proposals now
  collapses them (when real checks share the page) and offers *Activate all* /
  *Dismiss all* behind count-stating confirm dialogs, backed by a new
  transactional `POST /checks/bulk-transition` that moves each id only while it
  is still `proposed`, so a concurrent single-item action cannot be clobbered.
  (#279)
- `GET /checks/{id}`, so the check-detail page no longer downloads the entire
  checks list. (#280)
- `GET /exceptions/view-counts`, so a saved-view chip's badge is the count that
  clicking it actually produces — computed in one conditional-aggregate query,
  grant-scoped, and honouring a pinned dataset/run/check workspace. (#276)
- **Per-connection grants are editable in the UI** (Settings → Users → *Manage
  access*); previously the grants API was reachable only by hand. (#288)
- **Structured root-cause reports.** `rca_sessions.report_json` (migration
  `0012_rca_report_json`) stores the agent's findings as data — likely cause,
  confidence, hypotheses with verdicts, evidence items carrying their SQL, and
  recommended actions — and the UI renders them instead of a wall of markdown.
  Previously the frontend consumed a `report_json` the backend never wrote. (#287)
- `frontend/src/lib/useUnsavedGuard.ts` — the shared unsaved-changes guard now
  wired into the dashboard, knowledge, contract and dataset editors, so in-app
  navigation (`<Link>` and `useNavigate()` alike) asks before discarding analyst
  typing, alongside the existing `beforeunload` cover for tab close. **Known
  limitation:** browser Back/Forward is not intercepted — the app mounts a plain
  `<BrowserRouter>`, which offers no cancellable hook for `popstate`. (#284)
- `backend/app/api/_filters.py` — the single chokepoint every `?q=` filter builds
  its pattern with: `contains_pattern()` for a substring match, bare
  `escape_like()` where the call site needs a different shape, and
  `escape=LIKE_ESCAPE` at every call site.
- Weekly Dependabot updates for pip, npm and GitHub Actions, staggered across the
  week and grouped so routine churn arrives as one PR, plus an advisory
  `dependency-audit` CI job (`pip-audit` over `backend/requirements.lock`, `npm
  audit --omit=dev`). It runs on every PR **and on a weekly cron** — a CVE is
  disclosed against code that did not change, so a commit-triggered job never
  fires when it matters. On the scheduled trigger only the audit runs; the four
  heavy jobs are gated off it, because rebuilding an unchanged `main` proves
  nothing. The job is `continue-on-error`, so a fresh disclosure surfaces without
  turning every PR red until an upstream fix exists. (#292)
- CI additionally runs migrations up/down/up against real PostgreSQL (#291) and
  builds the shipped images, boots the compose stack under the `DQ_ENV=prod`
  posture and drives `scripts/e2e_smoke.py` through it (#292).

### Changed

- **List endpoints no longer issue N+1 queries.** `/runs` and `/datasets` fold
  their per-row counts into one `GROUP BY` plus eager loads, and exception
  serialization prefetches the checks/datasets/users it resolves; query budgets
  are pinned by `tests/test_query_counts.py`. (#280)
- Vite splits the react / recharts / tanstack vendors into their own chunks
  (entry 1.35 MB → 871 kB), and nginx serves content-hashed `/assets/` as
  immutable for a year while `index.html` is `no-cache` — a cached index used to
  keep pointing at deleted bundles after a deploy. (#280)
- TanStack Query never retries permanent 4xx and uses `networkMode: "always"`
  for queries, so a same-origin API call can no longer be *paused* into an
  eternal spinner; reconnect refetching is restored explicitly and mutations keep
  the default online mode. (#277)
- **Reproducible backend images.** `backend/requirements.lock` pins the resolved
  dependency set; the Dockerfile installs the lock first (its own cache layer)
  and then the app itself with `--no-deps`, so two builds of the same commit no
  longer differ because `>=` floors resolved on different days. (#290)
- Built-in catalog datasets are generated into a shared named volume
  (`DQ_CATALOG_DATA_DIR`), so the api and worker containers see the same files
  instead of each writing its own copy. (#261)
- README / AGENTS.md / this changelog re-verified against the code: the check
  registry is 14 types, the dataset page has 12 tabs, the LLM layer is
  provider-agnostic, the smoke test makes 38 assertions, and the audit log and
  Alembic migrations moved from "roadmap" to "shipped". Re-verified a second time
  after review, because the first pass documented security fixes as originally
  written rather than as remediated — the docs now describe both defence layers,
  name the guard as a backstop rather than the control, and describe the grant
  sweep as an invariant instead of a list that goes stale on the next router.
  (#295)

### Fixed

- Deep links survive login: a logged-out visit to any app URL returns to that URL
  after signing in (router state, plus a validated `?from=` fallback on the hard
  401 redirect) instead of always dumping the user on the home page. (#276)
- Dead-end states: `/datasets/<bad-id>` and `/runs/<bad-id>` render a designed
  not-found instead of spinning forever, and a failing lineage query shows its
  error instead of a blank pane. (#277)
- Monitoring honesty: a `next_run_at` in the past is shown as **overdue**
  ("scheduler idle or behind") rather than a plain date; errored runs explain
  that the run did not complete and link to the source connection; the fleet
  health figure states how old the probe is, and never-probed rows say "not
  checked". (#278)
- The contract schema-column editor no longer loses input focus on every
  keystroke (rows carry a stable client-side id instead of being keyed by their
  own contents). (#283)
- Check mutations invalidate the dataset queries they affect, so dataset headers
  and list counts stop showing pre-mutation numbers. (#285)
- Bulk-triage selection is pruned after the action, so it can no longer re-target
  rows that have left the view, and a failed CSV export raises a toast instead of
  failing silently. (#286)
- Locally stored analyst preferences (saved views, hidden columns, worksheet tabs,
  query history) are namespaced per user, so a second sign-in on a shared machine
  no longer inherits the previous user's state; timestamps name their time zone.
  (#294)
- Honest copy and states across the surfaces the review rounds exercised:
  unregistering a dataset no longer trips the unsaved-changes guard (answering
  "keep editing" stranded the analyst on a deleted dataset); contract deletion no
  longer promises a restore path that does not exist; a custom-dashboard SQL
  widget distinguishes "the connection was deleted" from "you are not authorized",
  and the dashboards list stops offering viewers rows that would 403; editing an
  SLA no longer discards the typed edits its own Cancel protects; the home KPI
  advertises MTTR only, because nothing computes MTTD; and the assistant's live
  region no longer replays history when the session is switched.

### Security

- **A source database can no longer read the host it runs on.** Two layers ship,
  and they are not interchangeable:
  - *Driver level — the control that actually holds on DuckDB.* DuckDB
    connections now open with `enable_external_access=false` alongside
    `read_only=True` (`backend/app/connectors/dialects.py`). `read_only=True`
    stops writes only; DuckDB's **replacement scan** turns a bare string literal
    in table position into a host-file read — `SELECT * FROM '/etc/passwd'` —
    with no function call for any denylist to match. The switch disables every
    file/network access originating from SQL: replacement scans, `glob()`,
    `read_csv`/`read_parquet`, httpfs, `ATTACH`. **What it costs:** a registered
    DuckDB source can now read nothing but the `.duckdb` file its DSN names, so
    ad-hoc `read_csv('…')`, S3/HTTP reads and cross-file `ATTACH` stop working
    through a DQ Sentinel connection. That trade is deliberate.
  - *Statement level — an engine-agnostic backstop.* `guard_sql()` now also
    denies read-side functions that escape the database (`read_text`, `read_csv`,
    `glob`, `load_file`, `postgres_scan`, `xp_cmdshell`, …) and rejects a string
    literal in table position after `FROM`/`JOIN`. `SELECT read_text('/etc/passwd')`
    is a single SELECT with no write keyword, so the previous keyword denylist let
    it straight through. **Known limitation:** this is regular-expression matching
    over masked SQL, not a parser. It exists to cover engines that grow the same
    feature and to fail closed on obvious shapes — it is not what stands between
    DuckDB and the filesystem.

  Ordinary analyst SQL stays legal: a quoted identifier is unwrapped only when it
  is a single bare word (`SELECT "URL (raw)"`, `[Cluster (k)]` remain columns, not
  calls), CTE and table-alias **column lists** (`WITH url (id, addr) AS (…)`) are
  exempted structurally, and the standard `FROM`-inside-a-call forms
  (`EXTRACT(YEAR FROM '2024-01-01')`, `SUBSTRING(x FROM 'y')`, `TRIM(BOTH FROM
  ' x ')`, `OVERLAY(… FROM 2)`) are exempted from the table-position rule.
  (#281, #267)
- **Unhandled exceptions return a clean JSON 500** carrying the request id, via a
  global ASGI middleware; the traceback goes to the server log only. A malformed
  JWT subject now returns 401 rather than 500. (#282)
- **`?q=` filters escape LIKE wildcards.** A typed `%` or `_` matched every row —
  an over-broad read on grant-scoped surfaces, and a backtracking hazard. Every
  `?q=` now builds its pattern in `backend/app/api/_filters.py` and passes
  `escape=LIKE_ESCAPE`, including the audit log's *prefix* filter (which needs
  bare `escape_like()` rather than `contains_pattern()`, since a leading `%`
  would silently widen it). (#273, #282)
- **Connection grants are enforced wherever connection-scoped rows are read or
  written.** The sweep started with the global surfaces that returned rows from
  connections the caller had no grant on — saved queries, ad-hoc dashboards,
  custom dashboards, lineage — and review then found data contracts skipped
  entirely, where a global editor could author *and activate* a contract on an
  ungranted connection: a cross-connection **write**, since activation
  materializes scheduled checks. Deliberately not enumerated here, because that
  list has grown every time anyone looked: the invariant is that an endpoint
  touching connection-scoped rows resolves through `backend/app/security.py` —
  `visible_connection_ids()` / `visible_dataset_ids()` to filter a list,
  `assert_connection_visible()` / `assert_dataset_visible()` for a read by id
  (404, never 403, so ids can't be probed), `assert_connection_role()` for a
  mutation. `backend/tests/test_router_scoping.py` pins each endpoint found so
  far; assume a newly added router is unscoped until it is checked. (#72, #159)
- **Containers run unprivileged**: the backend image creates and drops to a
  dedicated non-root user, the frontend image is built on
  `nginx-unprivileged`, and compose publishes the API on `127.0.0.1:8000` only,
  because `/metrics` is unauthenticated by design. (#292)

## [0.1.0] - 2026-07-02

Initial public release.

### Added

- **Connections** to SQLite, DuckDB, PostgreSQL, MySQL, SQL Server, Snowflake,
  BigQuery, Trino, and ClickHouse via SQLAlchemy DSNs. Non-core drivers are
  optional extras. Every source query passes a read-only SQL safety guard
  (single SELECT/CTE, denylist, forced row limit).
- **Profiling**: per-column stats pushed down as SQL aggregates plus sampled
  quantiles, string-format inference (email/uuid/url/date), primary-key and
  freshness candidates.
- **Check generation**: deterministic heuristics always; LLM-proposed checks
  with an API key, optionally preceded by a bounded read-only exploration
  agent. Provider-agnostic LLM layer: native Anthropic or any OpenAI-compatible
  endpoint (OpenRouter, vLLM, Ollama, ...).
- **Check types** (14, pluggable registry): `not_null`, `unique`,
  `accepted_values`, `range`, `string_length`, `regex_match`,
  `schema_contract`, `freshness`, `row_count_min`, `row_count_anomaly`,
  `custom_sql`, `ml_outlier` (IsolationForest), `distribution_drift` (PSI/KS),
  and `schema_change`.
- **Scheduler worker**: interval + cron schedules, multi-worker-safe claiming
  on PostgreSQL, graceful shutdown, Prometheus metrics.
- **Exception triage**: violating rows captured per failed run; lifecycle
  open → acknowledged / expected / resolved / muted with notes; "expected"
  markings feed the table knowledge base. Bulk triage, saved views, keyboard
  shortcuts, column attribution ("why it failed") drawer.
- **Root-cause analysis agent**: bounded LLM tool-use loop that investigates a
  failure with read-only SQL and produces an evidence-backed report with the
  full query transcript.
- **Assistant chat**: WebSocket-streamed agent with dataset/failure/SQL/chart
  tools; sessions persist.
- **Lineage**: table-level graphs parsed from view SQL (sqlglot) with a
  check-health overlay.
- **SQL workbench** with schema sidebar, history, saved queries, CSV export.
- **Dashboards**: custom widget dashboards plus LLM-generated ad-hoc
  dashboards.
- **Governance & operations**: quality scorecards, SLA tracking, incidents,
  data contracts, monitor packs, schema-change monitoring, a read-only status
  page, insights, notifications (Slack, email, generic webhook, Teams,
  PagerDuty, Jira, ServiceNow), audit log, global search.
- **AuthN/Z**: JWT auth with viewer/editor/admin roles and per-connection
  grants.
- **Observability**: structured logs with request-ID correlation, Prometheus
  metrics, Grafana dashboard, docker-compose stack (API, worker, frontend,
  PostgreSQL; Prometheus/Grafana/Loki behind an opt-in `monitoring` profile).

[Unreleased]: https://github.com/k-sandhu/dq-sentinel/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/k-sandhu/dq-sentinel/releases/tag/v0.1.0
