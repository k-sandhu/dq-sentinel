# Changelog

All notable changes to DQ Sentinel are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/) with the usual pre-1.0 caveat that minor versions
may contain breaking changes.

## [Unreleased]

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
- **Check types** (pluggable registry): `not_null`, `unique`,
  `accepted_values`, `range`, `string_length`, `regex_match`, `freshness`,
  `row_count_min`, `row_count_anomaly`, `custom_sql`, `ml_outlier`
  (IsolationForest).
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
