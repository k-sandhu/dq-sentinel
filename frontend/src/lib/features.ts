// Curated inventory of what DQ Sentinel can do today, grouped by area. This
// powers the standalone /features page (a quick "what have we built?" map).
//
// Keep it honest: only list capabilities that actually ship. When you build a
// new feature, add it here; `to` deep-links into the app and `ai` flags LLM-backed
// features (which degrade gracefully without a key).

export interface FeatureItem {
  title: string;
  description: string;
  to?: string; // in-app route this feature lives at
  ai?: boolean; // uses the optional LLM layer
}

export interface FeatureSection {
  title: string;
  icon: string; // ui.tsx Icon name
  blurb: string;
  items: FeatureItem[];
}

export const FEATURE_SECTIONS: FeatureSection[] = [
  {
    title: "Connect & ingest",
    icon: "db",
    blurb: "Register read-only data sources and the tables/views you want to watch.",
    items: [
      {
        title: "9 database engines",
        description:
          "SQLite, DuckDB, PostgreSQL, MySQL, SQL Server, Snowflake, BigQuery, Trino and ClickHouse via a SQLAlchemy dialect registry. Non-core drivers are optional pip extras.",
        to: "/connections",
      },
      {
        title: "Read-only SQL safety guard",
        description:
          "Every query — human or LLM — passes a guard that allows a single SELECT/CTE, denies mutations, and forces a row limit. Sources open read-only where the driver supports it.",
      },
      {
        title: "Connection fleet health",
        description:
          "Live health probe across all connections, surfaced as a status pill in the top bar and a dashboard.",
        to: "/connections",
      },
      {
        title: "Schema & catalog browser",
        description: "Browse schemas, tables, views and columns on a source before registering datasets.",
        to: "/connections",
      },
      {
        title: "Datasets",
        description: "Register tables and views as first-class datasets with a tabbed detail view.",
        to: "/datasets",
      },
    ],
  },
  {
    title: "Profiling",
    icon: "table",
    blurb: "Understand a table before you write a single check.",
    items: [
      {
        title: "SQL-pushdown column profiling",
        description:
          "Per-column stats computed as SQL aggregates plus sampled quantiles — null rates, distinct counts, min/max, distributions.",
        to: "/datasets",
      },
      {
        title: "Format & candidate inference",
        description:
          "String-format detection (email/uuid/url/date) plus primary-key and freshness-column candidates suggested from the profile.",
        to: "/datasets",
      },
    ],
  },
  {
    title: "Checks & detection",
    icon: "shield",
    blurb: "A pluggable check registry, generated heuristically or by an LLM.",
    items: [
      {
        title: "14 built-in check types",
        description:
          "not_null, unique, accepted_values, range, string_length, regex_match, freshness, row_count_min, row_count_anomaly, custom_sql, ml_outlier, distribution_drift, schema_change and schema_contract.",
        to: "/checks",
      },
      {
        title: "Heuristic check generation",
        description: "A deterministic engine proposes sensible checks from the profile — always available, no API key needed.",
        to: "/checks",
      },
      {
        title: "LLM check generation",
        description:
          "With a key, the model proposes sharper checks using the profile plus the table knowledge you record. Validated against the registry.",
        to: "/checks",
        ai: true,
      },
      {
        title: "Exploration agent",
        description:
          "A bounded tool-use loop that writes its own read-only SQL to learn the data (distributions, cross-column consistency, orphans) before proposing checks.",
        ai: true,
      },
      {
        title: "ML outlier detection",
        description:
          "IsolationForest over numeric columns as a first-class check type; flagged rows flow into the exception workflow with anomaly scores.",
        to: "/checks",
      },
      {
        title: "Custom SQL checks",
        description: "Author your own violation query for anything the built-ins don't cover.",
        to: "/checks",
      },
    ],
  },
  {
    title: "Schedule & run",
    icon: "play",
    blurb: "Run checks on a schedule and keep a history of every result.",
    items: [
      {
        title: "Scheduler worker",
        description:
          "A worker process claims due checks (interval or cron schedules), multi-worker safe on Postgres, and records runs with metrics history.",
        to: "/runs",
      },
      {
        title: "Run history & metrics",
        description: "Every execution is a CheckRun with status, timing, violation counts and a metric trend.",
        to: "/runs",
      },
    ],
  },
  {
    title: "Exceptions & triage",
    icon: "alert",
    blurb: "Turn failures into a Metabase-style triage workflow.",
    items: [
      {
        title: "Violating-row capture",
        description: "Failed runs capture sample violating rows with reasons (and scores for ML checks).",
        to: "/exceptions",
      },
      {
        title: "Triage lifecycle",
        description:
          "Move exceptions open → acknowledged / expected / resolved / muted with notes. 'Expected' markings feed the table knowledge base.",
        to: "/exceptions",
      },
      {
        title: "Bulk actions, saved views & shortcuts",
        description: "Filter, save views, act in bulk and fly through triage with keyboard shortcuts.",
        to: "/exceptions",
      },
    ],
  },
  {
    title: "Incidents & reliability",
    icon: "bolt",
    blurb: "Roll failures up into incidents and track reliability against SLAs.",
    items: [
      {
        title: "Incidents",
        description: "Group related failures into incidents with their own lifecycle and timeline.",
        to: "/incidents",
      },
      {
        title: "Reliability & SLAs",
        description: "SLA attainment and breach tracking with reliability rollups and scorecards.",
        to: "/reliability",
      },
    ],
  },
  {
    title: "Lineage",
    icon: "graph",
    blurb: "See how tables and views connect — and where quality breaks down.",
    items: [
      {
        title: "View-derived lineage graph",
        description:
          "sqlglot parses view definitions into a table-level (and column-level) lineage graph, colored by check health with a 'needs attention' jump list.",
        to: "/lineage",
      },
    ],
  },
  {
    title: "AI & agents",
    icon: "chat",
    blurb: "Provider-agnostic LLM features that degrade gracefully without a key.",
    items: [
      {
        title: "Root-cause analysis agent",
        description:
          "A tool-use agent reproduces a failure, segments and time-boxes the bad rows, queries related tables and submits an evidence-backed markdown report with its full SQL transcript.",
        ai: true,
      },
      {
        title: "Assistant chat",
        description:
          "A streaming WebSocket assistant with tools — dataset overview, recent failures, run_sql, get_table_code and inline chart rendering.",
        to: "/assistant",
        ai: true,
      },
      {
        title: "Provider-agnostic LLM layer",
        description:
          "Native Anthropic API or any OpenAI-compatible endpoint (OpenRouter, vLLM, Ollama, Together…). PII columns are redacted from every prompt.",
        to: "/settings",
        ai: true,
      },
      {
        title: "MCP connector",
        description: "Model Context Protocol support on the Anthropic path for richer tool access.",
        ai: true,
      },
    ],
  },
  {
    title: "Dashboards & exploration",
    icon: "grid",
    blurb: "Explore the data and build your own views.",
    items: [
      {
        title: "Custom dashboards",
        description: "Build dashboards from metric, checks, exceptions, SQL and note widgets.",
        to: "/dashboards",
      },
      {
        title: "SQL workbench",
        description: "A guarded SQL editor with result grid, query history and saved queries.",
        to: "/workbench",
      },
      {
        title: "Global search & command palette",
        description: "Jump to any dataset, check, connection or saved query — press / or Ctrl/Cmd+K anywhere.",
      },
    ],
  },
  {
    title: "Contracts & knowledge",
    icon: "book",
    blurb: "Capture intent and institutional memory next to the data.",
    items: [
      {
        title: "Schema contracts",
        description: "Declare a dataset's expected schema and track conformance over time.",
        to: "/datasets",
      },
      {
        title: "Table knowledge base",
        description:
          "Record business context, known issues, SLAs and PII columns per table. Feeds LLM prompts (PII redacted) and improves generated checks.",
        to: "/datasets",
      },
      {
        title: "Monitor packs",
        description: "Bundle related monitors so a dataset gets a coherent set of checks at once.",
        to: "/datasets",
      },
    ],
  },
  {
    title: "Alerting & integrations",
    icon: "alert",
    blurb: "Get told when something breaks, in the tools you already use.",
    items: [
      {
        title: "Notification channels",
        description:
          "Rule-driven alerts to Slack, email (SMTP), generic signed webhooks, Microsoft Teams, PagerDuty, Jira and ServiceNow.",
        to: "/settings",
      },
    ],
  },
  {
    title: "Platform & operations",
    icon: "settings",
    blurb: "The plumbing that keeps it production-shaped.",
    items: [
      {
        title: "Auth & RBAC",
        description: "JWT auth with viewer / editor / admin roles gating reads, mutations and admin actions.",
        to: "/settings",
      },
      {
        title: "Audit log",
        description: "A retained, queryable record of who changed what.",
        to: "/settings",
      },
      {
        title: "Observability",
        description:
          "Prometheus metrics (API + worker), structured request-correlated logging, and a provisioned Grafana/Loki stack.",
      },
      {
        title: "Database migrations",
        description: "Alembic migrations run automatically on startup; a test guards against schema drift.",
      },
      {
        title: "Personalization",
        description: "Dark mode, comfortable/compact density, dataset favorites and a configurable landing page — per browser.",
        to: "/settings",
      },
    ],
  },
];
