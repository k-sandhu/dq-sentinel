/**
 * queryKeys.ts — single source of truth for TanStack Query keys (FE-4).
 *
 * Every query/mutation-invalidation key in the app is produced here through the
 * `qk` factory. As each surface migrates, its inline bracket-string key literals
 * (e.g. `["datasets", id]`) are replaced with the matching `qk.<family>.<accessor>()`,
 * so there is one place to see — and change — the key for any resource.
 *
 * Rules:
 *  - The factory returns ONLY keys. Per-query options (staleTime / gcTime /
 *    enabled / retry / refetchInterval / placeholderData / select) stay at the
 *    call site; migrating a key must never change a query's options. The options
 *    observed per family today are catalogued in docs/ via the D4 mapping, so a
 *    surface PR can preserve them when it migrates.
 *  - Each family exposes an `all` prefix tuple plus accessors that reproduce the
 *    EXACT segment order observed at call sites. Partial-key invalidations target
 *    `qk.<family>.all`.
 *  - Dynamic segments are function params, typed `number | string` where call
 *    sites vary; object segments mirror the observed `{ ... }` shape so the key
 *    hashes identically to the literal it replaces.
 *  - `as const` everywhere so keys are readonly tuples and stay literal-typed.
 *
 * NOTE: where a family is observed with two divergent shapes in the wild (e.g.
 * `checks` keyed by `{ datasetId }` in some places and a bare number in others),
 * the factory faithfully reproduces BOTH so migration is behaviour-preserving —
 * it deliberately does not "fix" a pre-existing cache-key inconsistency.
 */

/** Dataset/connection/run/etc. ids appear as Number(...) or sometimes string. */
type Id = number | string;

/** Workbench "suggest" object segment (POST-as-read). The optional context ids
 *  arrive straight from URL params as `number | undefined`, so they must accept
 *  undefined — the segment is hashed verbatim (undefined stays distinct from null). */
type SuggestScope = {
  connectionId: Id | null;
  datasetId: Id | null | undefined;
  runId: Id | null | undefined;
  exceptionId: Id | null | undefined;
  checkId: Id | null | undefined;
};

// ---------------------------------------------------------------------------
// qk factory
// ---------------------------------------------------------------------------

export const qk = {
  // === Datasets =========================================================
  // [datasets] list, [datasets, id] detail, [datasets, { connectionId }] scoped list.
  datasets: {
    all: ["datasets"] as const,
    list: () => ["datasets"] as const,
    detail: (id: Id) => ["datasets", id] as const,
    byConnection: (connectionId: Id | null) =>
      ["datasets", { connectionId }] as const,
  },

  // === Profiling / exploration / preview / schema =======================
  profile: {
    all: ["profile"] as const,
    detail: (datasetId: Id) => ["profile", datasetId] as const,
  },
  exploration: {
    all: ["exploration"] as const,
    detail: (datasetId: Id) => ["exploration", datasetId] as const,
  },
  preview: {
    all: ["preview"] as const,
    detail: (datasetId: Id) => ["preview", datasetId] as const,
  },
  schemaHistory: {
    all: ["schema-history"] as const,
    detail: (datasetId: Id) => ["schema-history", datasetId] as const,
  },

  // === Checks ===========================================================
  // Multiple observed shapes: bare list, object-scoped, bare-id-scoped,
  // filter-scoped, and the literal "active" sub-list. `all` covers prefix
  // invalidation (used widely on mutation success).
  checks: {
    all: ["checks"] as const,
    byDatasetObj: (datasetId: Id | null | undefined) =>
      ["checks", { datasetId }] as const,
    byDatasetId: (datasetId: Id) => ["checks", datasetId] as const,
    byFilter: (filter: unknown) => ["checks", { filter }] as const,
    active: () => ["checks", "active"] as const,
  },
  checkDetail: {
    all: ["check-detail"] as const,
    detail: (checkId: Id) => ["check-detail", checkId] as const,
  },
  checkTypes: {
    all: ["check-types"] as const,
    list: () => ["check-types"] as const,
  },
  columns: {
    all: ["columns"] as const,
    detail: (datasetId: Id) => ["columns", datasetId] as const,
  },

  // === Contracts ========================================================
  contract: {
    all: ["contract"] as const,
    detail: (datasetId: Id) => ["contract", datasetId] as const,
  },
  contractConformance: {
    all: ["contract-conformance"] as const,
    detail: (datasetId: Id, contractId: Id | undefined) =>
      ["contract-conformance", datasetId, contractId] as const,
  },
  contractExport: {
    all: ["contract-export"] as const,
    detail: (datasetId: Id, contractId: Id | undefined) =>
      ["contract-export", datasetId, contractId] as const,
  },
  contractVersions: {
    all: ["contract-versions"] as const,
    detail: (datasetId: Id, contractId: Id | undefined) =>
      ["contract-versions", datasetId, contractId] as const,
  },
  contractDiff: {
    all: ["contract-diff"] as const,
    detail: (
      datasetId: Id,
      contractId: Id | undefined,
      fromVersionId: Id | undefined,
      toVersionId: Id | undefined,
    ) =>
      ["contract-diff", datasetId, contractId, fromVersionId, toVersionId] as const,
  },

  // === Knowledge / DDL / lineage (dataset tabs) =========================
  knowledge: {
    all: ["knowledge"] as const,
    detail: (datasetId: Id) => ["knowledge", datasetId] as const,
  },
  datasetDdl: {
    all: ["dataset-ddl"] as const,
    detail: (datasetId: Id) => ["dataset-ddl", datasetId] as const,
  },
  lineageDataset: {
    all: ["lineage-dataset"] as const,
    detail: (datasetId: Id, depth: number, granularity: "table" | "column") =>
      ["lineage-dataset", datasetId, depth, granularity] as const,
  },
  monitorPack: {
    all: ["monitor-pack"] as const,
    detail: (datasetId: Id) => ["monitor-pack", datasetId] as const,
  },

  // === Ad-hoc dashboards (dataset tab) ==================================
  adhoc: {
    all: ["adhoc"] as const,
    byDataset: (datasetId: Id | null | undefined) =>
      ["adhoc", { datasetId }] as const,
  },
  adhocOpen: {
    all: ["adhoc-open"] as const,
    detail: (openId: Id | null) => ["adhoc-open", openId] as const,
  },

  // === RCA ==============================================================
  rca: {
    all: ["rca"] as const,
    byDataset: (datasetId: Id) => ["rca", datasetId] as const,
  },

  // === Runs =============================================================
  // [runs] prefix; list-by-querystring, detail-by-id, object-scoped variants.
  runs: {
    all: ["runs"] as const,
    list: (query: string) => ["runs", query] as const,
    detail: (runId: Id) => ["runs", runId] as const,
    byDataset: (datasetId: Id | null | undefined) =>
      ["runs", { datasetId }] as const,
    byCheck: (checkId: Id | null | undefined) =>
      ["runs", { checkId }] as const,
  },
  runExceptions: {
    all: ["run-exceptions"] as const,
    detail: (runId: Id) => ["run-exceptions", runId] as const,
  },

  // === Exceptions =======================================================
  exceptions: {
    all: ["exceptions"] as const,
    list: (listParams: string) => ["exceptions", listParams] as const,
  },
  exceptionsFacets: {
    all: ["exceptions-facets"] as const,
    list: (apiParams: string) => ["exceptions-facets", apiParams] as const,
  },
  exceptionViewCounts: {
    all: ["exception-view-counts"] as const,
    get: (pinnedParams: string) => ["exception-view-counts", pinnedParams] as const,
  },
  exceptionEvents: {
    all: ["exception-events"] as const,
    detail: (exceptionId: Id) => ["exception-events", exceptionId] as const,
  },
  exceptionAttribution: {
    all: ["exception-attribution"] as const,
    detail: (exceptionId: Id) => ["exception-attribution", exceptionId] as const,
  },
  assignees: {
    all: ["assignees"] as const,
    list: () => ["assignees"] as const,
  },

  // === Incidents ========================================================
  incidents: {
    all: ["incidents"] as const,
    list: (listParams: string) => ["incidents", listParams] as const,
  },
  incidentDetail: {
    all: ["incident-detail"] as const,
    detail: (id: Id | undefined) => ["incident-detail", id] as const,
  },

  // === Built-in data catalog ===========================================
  catalog: {
    all: ["catalog"] as const,
    list: () => ["catalog"] as const,
  },

  // === Workbench: connections / schema / ddl / saved queries / suggest ==
  connections: {
    all: ["connections"] as const,
    list: () => ["connections"] as const,
  },
  schema: {
    all: ["schema"] as const,
    detail: (connectionId: Id | null) => ["schema", connectionId] as const,
  },
  ddl: {
    all: ["ddl"] as const,
    detail: (connectionId: Id | null, table: string | null) =>
      ["ddl", connectionId, table] as const,
  },
  savedQueries: {
    all: ["saved-queries"] as const,
    byConnection: (connectionId: Id | null) =>
      ["saved-queries", { connectionId }] as const,
    byDataset: (datasetId: Id | null | undefined) =>
      ["saved-queries", { datasetId }] as const,
  },
  savedQuery: {
    all: ["saved-query"] as const,
    detail: (savedQueryId: Id | null) => ["saved-query", savedQueryId] as const,
  },
  suggest: {
    all: ["suggest"] as const,
    detail: (scope: SuggestScope) => ["suggest", scope] as const,
  },

  // === Lineage / connections (top-level pages) ==========================
  connectionLineage: {
    all: ["connection-lineage"] as const,
    detail: (connectionId: Id | null, granularity: "table" | "column") =>
      ["connection-lineage", connectionId, granularity] as const,
  },
  engines: {
    all: ["engines"] as const,
    list: () => ["engines"] as const,
  },
  fleetHealth: {
    all: ["fleet-health"] as const,
    list: () => ["fleet-health"] as const,
  },
  tables: {
    all: ["tables"] as const,
    detail: (id: Id) => ["tables", id] as const,
  },

  // === Assistant / chat / app health ====================================
  health: {
    all: ["health"] as const,
    get: () => ["health"] as const,
  },
  chatSessions: {
    all: ["chat-sessions"] as const,
    list: () => ["chat-sessions"] as const,
  },

  // === Dashboards (home / console / custom) =============================
  dashboard: {
    all: ["dashboard"] as const,
    summary: () => ["dashboard"] as const,
    console: () => ["dashboard", "console"] as const,
  },
  dashboardConsole: {
    all: ["dashboard-console"] as const,
    get: () => ["dashboard-console"] as const,
  },
  customDashboards: {
    all: ["custom-dashboards"] as const,
    list: () => ["custom-dashboards"] as const,
  },
  customDashboard: {
    all: ["custom-dashboard"] as const,
    detail: (dashboardId: Id) => ["custom-dashboard", dashboardId] as const,
  },
  scorecards: {
    all: ["scorecards"] as const,
    summary: () => ["scorecards", "summary"] as const,
  },

  // === Dashboard widgets ================================================
  widgetTrend: {
    all: ["widget-trend"] as const,
    detail: (qs: string) => ["widget-trend", qs] as const,
  },
  widgetMetric: {
    all: ["widget-metric"] as const,
    detail: (qs: string) => ["widget-metric", qs] as const,
  },
  widgetMatrix: {
    all: ["widget-matrix"] as const,
    detail: (qs: string) => ["widget-matrix", qs] as const,
  },
  widgetExceptions: {
    all: ["widget-exceptions"] as const,
    detail: (qs: string, limit: number) =>
      ["widget-exceptions", qs, limit] as const,
  },

  // === Reliability / SLA ================================================
  reliability: {
    all: ["reliability"] as const,
    get: () => ["reliability"] as const,
  },
  sla: {
    all: ["sla"] as const,
    detail: (id: Id) => ["sla", id] as const,
  },
  status: {
    all: ["status"] as const,
    get: () => ["status"] as const,
  },

  // === Docs =============================================================
  doc: {
    all: ["doc"] as const,
    detail: (slug: string) => ["doc", slug] as const,
  },
  docs: {
    all: ["docs"] as const,
    list: () => ["docs"] as const,
  },

  // === Search ===========================================================
  globalSearch: {
    all: ["global-search"] as const,
    detail: (term: string) => ["global-search", term] as const,
  },

  // === Settings: audit / mcp / notifications / users ====================
  audit: {
    all: ["audit"] as const,
    list: (
      entityType: string | null,
      action: string | null,
      sinceHours: number,
      offset: number,
    ) => ["audit", entityType, action, sinceHours, offset] as const,
  },
  mcpServers: {
    all: ["mcp-servers"] as const,
    list: () => ["mcp-servers"] as const,
  },
  notificationRules: {
    all: ["notification-rules"] as const,
    list: () => ["notification-rules"] as const,
  },
  users: {
    all: ["users"] as const,
    list: () => ["users"] as const,
  },
} as const;

/** The factory type — reference a family as `QueryKeyFactory["datasets"]`. */
export type QueryKeyFactory = typeof qk;
