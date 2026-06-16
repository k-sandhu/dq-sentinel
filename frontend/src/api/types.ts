// Mirrors backend/app/schemas.py — keep in sync when the API changes.

export type Role = "viewer" | "editor" | "admin";
export type Severity = "info" | "warn" | "error";
export type CheckStatus = "proposed" | "active" | "disabled" | "archived";
export type RunStatus = "pass" | "warn" | "fail" | "error";
export type ExceptionStatus = "open" | "acknowledged" | "expected" | "resolved" | "muted";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface TokenOut {
  access_token: string;
  token_type: string;
  user: User;
}

export interface Connection {
  id: number;
  name: string;
  kind: string;
  dsn_masked: string;
  created_at: string;
  dataset_count: number;
}

export interface ConnectionTest {
  ok: boolean;
  message: string;
  table_count: number | null;
}

export interface EngineInfo {
  kind: string;
  label: string;
  dsn_example: string;
  driver_installed: boolean;
  install_extra: string | null;
  notes: string | null;
}

export interface TableInfo {
  schema_name: string | null;
  table_name: string;
  kind: "table" | "view";
  registered_dataset_id: number | null;
}

export interface ColumnInfo {
  name: string;
  dtype: string;
  nullable: boolean;
}

export interface Dataset {
  id: number;
  connection_id: number;
  connection_name: string;
  schema_name: string | null;
  table_name: string;
  display_name: string;
  row_count: number | null;
  last_profiled_at: string | null;
  created_at: string;
  active_checks: number;
  open_exceptions: number;
  health: "pass" | "warn" | "fail" | "unknown" | null;
  importance: string | null;
  owner: string | null;
}

export interface Preview {
  columns: string[];
  rows: unknown[][];
  total_rows: number | null;
}

export interface TopValue {
  value: unknown;
  count: number;
}

export interface ColumnProfile {
  name: string;
  dtype: string;
  kind: "numeric" | "temporal" | "string" | "boolean" | "other";
  null_count: number;
  null_pct: number;
  distinct_count: number;
  distinct_pct: number;
  min?: unknown;
  max?: unknown;
  mean?: number | null;
  stddev?: number | null;
  quantiles: Record<string, number>;
  min_len?: number | null;
  avg_len?: number | null;
  max_len?: number | null;
  patterns: Record<string, number>;
  top_values: TopValue[];
  sample_values: unknown[];
}

export interface Profile {
  id: number;
  dataset_id: number;
  created_at: string;
  row_count: number;
  sampled_rows: number;
  columns: ColumnProfile[];
  table_facts: {
    pk_candidates?: string[];
    temporal_columns?: { name: string; max: string }[];
    column_count?: number;
  };
}

// ---- schema history (#101) ----
export interface SchemaColumn {
  name: string;
  dtype: string;
  nullable: boolean;
  ordinal: number;
}

export interface SchemaChangeSummary {
  added: string[];
  removed: string[];
  type_changed: number;
  nullability_changed: number;
  reordered: boolean;
}

export interface SchemaSnapshot {
  id: number;
  dataset_id: number;
  captured_at: string;
  source: string; // profile | check | baseline
  is_baseline: boolean;
  fingerprint: string;
  columns: SchemaColumn[];
  change_summary: SchemaChangeSummary | null; // vs the chronologically previous snapshot
}

export interface SchemaHistory {
  dataset_id: number;
  pinned_baseline_id: number | null;
  snapshots: SchemaSnapshot[]; // newest first
}

export interface Knowledge {
  dataset_id?: number;
  business_context: string;
  known_issues: string;
  importance: "low" | "medium" | "high" | "critical";
  owner: string;
  freshness_sla_hours: number | null;
  pii_columns: string[];
  notes: string;
  updated_at?: string | null;
}

export interface Check {
  id: number;
  dataset_id: number;
  dataset_name: string;
  name: string;
  check_type: string;
  column_name: string | null;
  params: Record<string, unknown>;
  severity: Severity;
  status: CheckStatus;
  origin: string;
  rationale: string;
  schedule_kind: string | null;
  schedule_expr: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
  created_at: string;
}

export interface CheckTypeInfo {
  key: string;
  label: string;
  description: string;
  needs_column: boolean;
  params: { name: string; type: string; required: boolean; default: unknown; description: string }[];
}

export interface GenerateResult {
  created: number;
  skipped_duplicates: number;
  mode: "llm" | "heuristic";
  explored: boolean;
  checks: Check[];
}

export interface Run {
  id: number;
  check_id: number;
  check_name: string;
  check_type: string;
  dataset_id: number;
  dataset_name: string;
  started_at: string;
  finished_at: string | null;
  status: RunStatus;
  violation_count: number;
  rows_evaluated: number | null;
  metrics: Record<string, unknown>;
  error_message: string;
  triggered_by: string;
  exception_count: number;
}

// ---- exceptions (workbench: #55 identity, #56 triage workflow, #57 API v2) ----
export interface ExceptionRecord {
  id: number;
  run_id: number;
  check_id: number;
  check_name: string;
  check_type: string;
  check_severity: Severity;
  column_name: string | null;
  dataset_id: number;
  dataset_name: string;
  row_data: Record<string, unknown>;
  reason: string;
  outlier_score: number | null;
  status: ExceptionStatus;
  note: string;
  marked_by: string | null;
  marked_at: string | null;
  created_at: string;
  // identity & recurrence (#55)
  fingerprint: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  last_run_id: number | null;
  occurrence_count: number;
  // triage workflow (#56)
  assigned_to_id: number | null;
  assigned_to: string | null;
}

export interface ExceptionEvent {
  id: number;
  exception_id: number;
  kind: string; // status | comment | assign | system
  from_status: string;
  to_status: string;
  comment: string;
  user: string | null; // resolved display name; null = system action
  created_at: string;
}

export interface Assignee {
  id: number;
  name: string;
  email: string;
}

// ---- exceptions API v2 (#57) ----
export interface FacetEntry {
  id: number;
  name: string;
  count: number;
}

export interface ExceptionFacets {
  status: Record<string, number>;
  severity: Record<string, number>;
  check_type: Record<string, number>;
  datasets: FacetEntry[];
  total: number;
}

export interface ExceptionPage {
  items: ExceptionRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface RcaHypothesis {
  statement: string;
  verdict: "supported" | "refuted" | "inconclusive";
  evidence: string;
}

export interface RcaEvidence {
  title: string;
  sql: string;
  finding: string;
}

export interface RcaAction {
  action: string;
  kind: "fix_data" | "fix_pipeline" | "adjust_check" | "investigate";
}

export interface RcaReport {
  version?: number;
  hypotheses?: RcaHypothesis[];
  evidence?: RcaEvidence[];
  likely_cause?: string;
  recommended_actions?: RcaAction[];
  confidence?: "low" | "medium" | "high";
}

export interface RcaSession {
  id: number;
  dataset_id: number;
  dataset_name: string;
  check_run_id: number | null;
  question: string;
  status: "running" | "complete" | "failed";
  report_md: string;
  report_json: RcaReport | null;
  root_cause_summary: string;
  transcript: TranscriptStep[];
  model: string;
  created_at: string;
  finished_at: string | null;
}

export interface TranscriptStep {
  type: "text" | "sql" | "result" | "final";
  content?: unknown;
  sql?: string;
  purpose?: string;
  error?: boolean;
}

export interface Exploration {
  insights: {
    title: string;
    detail: string;
    risk: "low" | "medium" | "high";
    column: string | null;
    suggested_check_type: string | null;
  }[];
  queries_run: number;
}

export interface TrendPoint {
  day: string;
  passed: number;
  warned: number;
  failed: number;
  errored: number;
}

export interface Dashboard {
  datasets: number;
  active_checks: number;
  proposed_checks: number;
  runs_24h: number;
  failing_checks: number;
  open_exceptions: number;
  llm_enabled: boolean;
  pass_rate_7d: number | null;
  trend: TrendPoint[];
  recent_runs: Run[];
  worst_datasets: Dataset[];
}

export interface Health {
  status: string;
  version: string;
  llm_enabled: boolean;
  llm_provider: string | null;
  llm_model: string | null;
}

// ---- workbench ----
export interface QueryRunResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
}

export interface SchemaTable {
  schema_name: string | null;
  table_name: string;
  kind: "table" | "view";
  columns: ColumnInfo[];
}

export interface Ddl {
  table_name: string;
  ddl: string;
  source: "database" | "synthesized";
}

export interface Suggestion {
  title: string;
  sql: string;
  rationale: string;
}

export interface SuggestResult {
  mode: "llm" | "heuristic";
  connection_id: number;
  suggestions: Suggestion[];
}

export interface ConnectionHealth {
  id: number;
  name: string;
  ok: boolean;
  message: string;
  latency_ms: number | null;
}

// ---- saved queries (team snippet library) ----
export interface SavedQuery {
  id: number;
  connection_id: number;
  dataset_id: number | null;
  name: string;
  description: string;
  sql: string;
  tags: string[];
  created_by_id: number | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  last_run_at: string | null;
}

// ---- MCP ----
export interface McpServer {
  id: number;
  name: string;
  url: string;
  description: string;
  enabled: boolean;
  has_token: boolean;
  created_at: string;
}

// ---- notification rules (issue #27) ----
export type NotifyChannel = "slack" | "email";

export interface NotificationRule {
  id: number;
  dataset_id: number | null; // null = all datasets
  dataset_name: string; // "" when dataset_id is null
  min_severity: Severity;
  channel: NotifyChannel;
  target: string; // webhook URL or comma-separated emails ("" = global Slack default)
  on_error_runs: boolean;
  enabled: boolean;
  created_at: string;
}

// ---- ad-hoc dashboards ----
export type VizType = "number" | "bar" | "line" | "area" | "pie" | "table";

export interface PanelViz {
  type: VizType;
  x: string | null;
  y: string | null;
}

export interface Panel {
  title: string;
  description: string;
  sql: string;
  viz: PanelViz;
  columns: string[];
  rows: unknown[][];
  error: string | null;
  elapsed_ms: number;
}

export interface AdhocDashboardMeta {
  id: number;
  dataset_id: number;
  title: string;
  focus: string;
  origin: "llm" | "heuristic";
  created_at: string;
  last_refreshed_at: string | null;
  panel_count: number;
}

export interface AdhocDashboard extends AdhocDashboardMeta {
  panels: Panel[];
}

// --- DDL & lineage (issue #51) ---
export type LineageHealth = "pass" | "warn" | "fail" | "unknown";

export interface LineageNode {
  id: string; // lowercased "schema.table" when schema_name is set, else "table"
  schema_name: string | null;
  table_name: string;
  kind: "table" | "view";
  dataset_id: number | null; // null = external / unregistered node
  health: LineageHealth;
  failing_checks: number;
  open_exceptions: number;
}

export interface LineageEdge {
  source: string; // upstream node id — data flows source -> target
  target: string; // the view selecting from source
}

export interface LineageGraph {
  nodes: LineageNode[];
  edges: LineageEdge[];
  parse_errors: number; // view definitions that could not be parsed
  truncated: boolean; // graph capped at 300 nodes
}

export interface DatasetDdl {
  dataset_id: number;
  ddl: string;
  source: "database" | "synthesized";
  kind: "table" | "view";
}

// ---- assistant chat ----
export type ChatStep =
  | { type: "text"; content: string }
  | { type: "sql"; sql: string; purpose: string }
  | { type: "result"; content: string; error: boolean }
  | { type: "tool"; name: string; content: Record<string, unknown> }
  | {
      type: "chart";
      title: string;
      sql: string;
      viz: PanelViz;
      columns: string[];
      rows: unknown[][];
      elapsed_ms: number;
    }
  | { type: "error"; content: string };

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  steps: ChatStep[];
  created_at: string;
}

export interface ChatSession {
  id: number;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatSessionDetail extends ChatSession {
  messages: ChatMessage[];
}

// server -> client WebSocket events
export type ChatWsEvent =
  | { type: "session"; session: Omit<ChatSession, "message_count">; messages: ChatMessage[] }
  | { type: "message_saved"; message: ChatMessage }
  | { type: "status"; state: "thinking" | "tool"; tool?: string; detail?: string }
  | { type: "step"; step: ChatStep }
  | { type: "assistant_message"; message: ChatMessage }
  | { type: "error"; detail: string }
  | { type: "done" };

// --- exceptions paged envelope (issue #57) ---
// GET /exceptions returns this envelope; metric/exceptions dashboard widgets read
// `total` (the count) and `items` (the rows) from it with their stored params, so
// a widget's number is always identical to the triage queue's for the same filters.
export interface ExceptionPage {
  items: ExceptionRecord[];
  total: number;
  limit: number;
  offset: number;
}

// ---- audit log (issue #30) ----
export interface AuditEntry {
  id: number;
  user_id: number | null;
  user: string | null; // resolved display name; null = system/anonymous
  action: string;
  entity_type: string;
  entity_id: number | null;
  detail: Record<string, unknown>;
  request_id: string;
  client_ip: string;
  created_at: string;
}

export interface AuditPage {
  items: AuditEntry[];
  total: number;
  limit: number;
  offset: number;
}

// --- custom dashboards (issues #67/#68) ---
// This Widget union mirrors backend/app/schemas.py (the authoritative contract).
export type Visibility = "private" | "team";
export type WidgetSpan = 1 | 2;
export type WidgetType = "metric" | "exceptions" | "checks" | "sql" | "note";

// `params` keys mirror the GET /exceptions query contract (#57); values are strings.
export type WidgetParams = Record<string, string>;

export interface WidgetBase {
  id: string; // client-generated uuid
  title: string;
  span: WidgetSpan;
}

export interface MetricWidget extends WidgetBase {
  type: "metric";
  config: { params: WidgetParams; warn_at: number | null; danger_at: number | null };
}

export interface ExceptionsWidget extends WidgetBase {
  type: "exceptions";
  config: { params: WidgetParams; limit: number }; // limit clamped 1..10
}

export interface ChecksWidget extends WidgetBase {
  type: "checks";
  config: { dataset_ids: number[]; only_failing: boolean }; // dataset_ids capped at 20
}

// Server-executed snapshot — written ONLY by POST /dashboards/custom/{id}/refresh.
export interface WidgetSnapshot {
  columns: string[];
  rows: unknown[][];
  refreshed_at: string; // UTC; the UI labels freshness
  error: string | null;
  elapsed_ms: number;
}

export interface SqlWidget extends WidgetBase {
  type: "sql";
  config: { connection_id: number; sql: string; viz: PanelViz };
  snapshot?: WidgetSnapshot | null;
}

export interface NoteWidget extends WidgetBase {
  type: "note";
  config: { markdown: string };
}

export type Widget = MetricWidget | ExceptionsWidget | ChecksWidget | SqlWidget | NoteWidget;

export interface DashboardLayout {
  version: 1;
  widgets: Widget[];
}

export interface CustomDashboardMeta {
  id: number;
  name: string;
  description: string;
  owner_id: number;
  owner_name: string;
  owner_active: boolean;
  visibility: Visibility;
  widget_count: number;
  created_at: string;
  updated_at: string;
}

export interface CustomDashboard extends CustomDashboardMeta {
  layout: DashboardLayout;
  can_edit: boolean;
}

// ---- global search (issue #43) ----
export interface SearchHit {
  type: "dataset" | "check" | "connection" | "saved_query";
  id: number;
  title: string;
  subtitle: string;
  url: string;
}

export interface SearchOut {
  hits: SearchHit[];
}
