# Getting started

This walks you from a fresh sign-in to your first triaged data-quality exception.

## 1. Sign in

Use the seeded admin (`admin@example.com` / `admin123` in local dev). The admin
is seeded from `DQ_BOOTSTRAP_ADMIN_PASSWORD` only when the user table is empty,
so the cleanest way to avoid the default is to set that variable before the
first boot. There is no password-change screen yet — to rotate an existing
password, an admin calls `PATCH /api/v1/auth/users/{id}` with
`{"password": "..."}`. **Settings → Users** covers the rest of user management
(invite, role, activate/deactivate, per-connection access). In a production
deployment (`DQ_ENV=prod`) the default secret key and admin password are
refused at boot — set `DQ_SECRET_KEY` and `DQ_BOOTSTRAP_ADMIN_PASSWORD`.

## 2. Connect a data source

**Connections → Add connection.** Paste a SQLAlchemy DSN. Sources are always
opened read-only where the driver allows, and every query passes a SQL safety
guard: a single `SELECT`/`WITH`, no side-effect keywords, no read-side functions
that reach the filesystem/network/OS, and a forced row limit.

Examples:

- SQLite (the bundled sample): `sqlite:///</absolute/path>/samples/shopdb.sqlite`
- PostgreSQL: `postgresql+psycopg://user:pass@host:5432/db`
- DuckDB: `duckdb:////absolute/path/to/file.duckdb`

Non-core engines (Snowflake, BigQuery, Trino, ClickHouse, MySQL, SQL Server)
need their optional driver installed — see the README's connector extras.

## 3. Register datasets and profile them

Open the connection, pick the tables/views you care about, and **Register**.
On a dataset, click **Profile now** to compute per-column stats: null rates,
distinct counts, sampled quantiles, inferred string formats
(email/uuid/url/date), and primary-key / freshness candidates.

## 4. Generate checks

On a profiled dataset, **Generate checks**. A deterministic heuristic engine
always proposes a starting set. With an LLM key configured (native Anthropic or
any OpenAI-compatible endpoint), the model proposes sharper checks using the
profile plus any table **knowledge** you record (business context, known issues,
SLAs, PII columns). Review the proposals and activate the ones you want — a long
proposal list starts collapsed, and *Activate all* / *Dismiss all* clear it in
one step.

The registry ships 14 check types: `not_null`, `unique`, `accepted_values`,
`range`, `string_length`, `regex_match`, `schema_contract`, `freshness`,
`row_count_min`, `row_count_anomaly`, `custom_sql`, `ml_outlier`,
`distribution_drift`, and `schema_change`.

## 5. Let the worker run them

Active checks carry an interval or cron schedule. The worker process claims due
checks and records a **run** each time, capturing violating rows as
**exceptions**.

## 6. Triage exceptions

Open **Exceptions**. Each failed run captures sample violating rows. Mark them
`acknowledged` / `expected` / `resolved` / `muted` with a note. Marking rows
`expected` feeds the table's knowledge base so the system learns what's normal.
The **why it failed** drawer contrasts failing vs healthy rows and ranks the
columns that best separate them.

## 7. When something breaks: root-cause analysis

From a failed run, start the **RCA agent** (needs an LLM key). It reproduces the
failure, segments and time-boxes the bad rows, queries related tables with
read-only SQL, and writes a structured, evidence-backed report: the likely cause
with a confidence level, hypotheses marked supported / refuted / inconclusive,
evidence items carrying the actual SQL and what it showed, and recommended
actions. The full query transcript is shown in the UI.

## Where to go next

- **Lineage** — table-level graph parsed from view SQL, with check-health overlay.
- **Workbench** — guarded SQL editor with schema sidebar, history, saved queries.
- **Dashboards / Scorecards / Reliability** — rollups, SLOs, and SLA attainment.
- **Incidents** — failures grouped into an owned, resolvable timeline.
- **Contract** and **Monitors** tabs on a dataset — pin an expected schema, and
  install the standard freshness/volume monitor pack in one click.
- **Catalog** — curated public datasets you can register in one click to try
  the whole loop without wiring up your own source.
- **Assistant** — a chat agent with dataset/failure/SQL/chart tools.
- **Status** — a read-only page you can share with data consumers.

Without an LLM key everything above still works — check generation falls back to
heuristics and the agentic features explain what they need.
