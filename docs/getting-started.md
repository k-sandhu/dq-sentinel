# Getting started

This walks you from a fresh sign-in to your first triaged data-quality exception.

## 1. Sign in

Use the seeded admin (`admin@example.com` / `admin123` in local dev). Change the
password from **Settings → Users** before sharing the instance. In a production
deployment (`DQ_ENV=prod`) these defaults are refused at boot — set
`DQ_SECRET_KEY` and `DQ_BOOTSTRAP_ADMIN_PASSWORD`.

## 2. Connect a data source

**Connections → Add connection.** Paste a SQLAlchemy DSN. Sources are always
opened read-only where the driver allows, and every query passes a SQL safety
guard (single `SELECT`/`WITH`, denylist, forced row limit).

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
always proposes a starting set. With an LLM key configured, Claude proposes
sharper checks using the profile plus any table **knowledge** you record
(business context, known issues, SLAs, PII columns). Review the proposals and
activate the ones you want.

Check types include `not_null`, `unique`, `accepted_values`, `range`,
`string_length`, `regex_match`, `freshness`, `row_count_min`,
`row_count_anomaly`, `distribution_drift`, `schema_change`, `custom_sql`, and
`ml_outlier`.

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
read-only SQL, and writes an evidence-backed report — with the full query
transcript shown in the UI.

## Where to go next

- **Lineage** — table-level graph parsed from view SQL, with check-health overlay.
- **Workbench** — guarded SQL editor with schema sidebar, history, saved queries.
- **Dashboards / Scorecards / Reliability** — rollups, SLOs, and SLA attainment.
- **Assistant** — a chat agent with dataset/failure/SQL/chart tools.

Without an LLM key everything above still works — check generation falls back to
heuristics and the agentic features explain what they need.
