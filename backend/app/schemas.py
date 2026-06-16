"""Pydantic request/response schemas. Mirror changes into frontend/src/api/types.ts."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Role = Literal["viewer", "editor", "admin"]
Severity = Literal["info", "warn", "error"]
CheckStatus = Literal["proposed", "active", "disabled", "archived"]
RunStatus = Literal["pass", "warn", "fail", "error"]
ExceptionStatus = Literal["open", "acknowledged", "expected", "resolved", "muted"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth / users ----
class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORMModel):
    id: int
    email: str
    name: str
    role: Role
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AssigneeOut(ORMModel):
    """Minimal active-user shape for the triage assignee picker (#56).

    Any authenticated user may list these; deliberately omits role/is_active so
    a non-admin assignee dropdown doesn't leak authorization state.
    """

    id: int
    name: str
    email: str


class UserCreate(BaseModel):
    email: EmailStr
    name: str = ""
    password: str = Field(min_length=8)
    role: Role = "viewer"


class UserUpdate(BaseModel):
    name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    role: Role | None = None
    is_active: bool | None = None


# ---- connections ----
class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    dsn: str = Field(description="SQLAlchemy URL, e.g. sqlite:///C:/data/shop.sqlite")


class ConnectionOut(ORMModel):
    id: int
    name: str
    kind: str
    dsn_masked: str = ""
    created_at: datetime
    dataset_count: int = 0


class ConnectionTestOut(BaseModel):
    ok: bool
    message: str
    table_count: int | None = None


class EngineInfo(BaseModel):
    """A supported source-engine kind + driver availability (GET /connections/engines)."""

    kind: str
    label: str
    dsn_example: str
    driver_installed: bool
    install_extra: str | None = None
    notes: str | None = None


class TableInfo(BaseModel):
    schema_name: str | None = None
    table_name: str
    kind: Literal["table", "view"] = "table"
    registered_dataset_id: int | None = None


class ColumnInfo(BaseModel):
    name: str
    dtype: str
    nullable: bool = True


# ---- datasets ----
class DatasetRegisterIn(BaseModel):
    connection_id: int
    tables: list[TableInfo]


class DatasetOut(ORMModel):
    id: int
    connection_id: int
    connection_name: str = ""
    schema_name: str | None
    table_name: str
    display_name: str
    row_count: int | None
    last_profiled_at: datetime | None
    created_at: datetime
    active_checks: int = 0
    open_exceptions: int = 0
    health: str | None = None  # pass | warn | fail | unknown
    importance: str | None = None  # from table knowledge
    owner: str | None = None


class PreviewOut(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int | None = None


# ---- profiles ----
class TopValue(BaseModel):
    value: Any
    count: int


class ColumnProfileOut(BaseModel):
    name: str
    dtype: str
    kind: Literal["numeric", "temporal", "string", "boolean", "other"]
    null_count: int
    null_pct: float
    distinct_count: int
    distinct_pct: float
    min: Any = None
    max: Any = None
    mean: float | None = None
    stddev: float | None = None
    quantiles: dict[str, float] = {}
    min_len: int | None = None
    avg_len: float | None = None
    max_len: int | None = None
    patterns: dict[str, float] = {}
    top_values: list[TopValue] = []
    sample_values: list[Any] = []


class ProfileOut(BaseModel):
    id: int
    dataset_id: int
    created_at: datetime
    row_count: int
    sampled_rows: int
    columns: list[ColumnProfileOut]
    table_facts: dict[str, Any] = {}


# ---- schema history (#101) ----
class SchemaColumnOut(BaseModel):
    name: str
    dtype: str
    nullable: bool
    ordinal: int


class SchemaChangeSummary(BaseModel):
    added: list[str] = []
    removed: list[str] = []
    type_changed: int = 0
    nullability_changed: int = 0
    reordered: bool = False


class SchemaSnapshotOut(BaseModel):
    id: int
    dataset_id: int
    captured_at: datetime
    source: str
    is_baseline: bool
    fingerprint: str
    columns: list[SchemaColumnOut]
    change_summary: SchemaChangeSummary | None = None  # vs the chronologically previous snapshot


class SchemaHistoryOut(BaseModel):
    dataset_id: int
    pinned_baseline_id: int | None = None
    snapshots: list[SchemaSnapshotOut]  # newest first


# ---- SLA tracking (#102) ----
SLAScope = Literal["dataset", "check"]
SLATargetType = Literal["freshness", "volume", "check_success"]
SLAWindow = Literal["rolling_7d", "rolling_30d"]


class SLAIn(BaseModel):
    name: str = ""
    scope: SLAScope = "dataset"
    scope_id: int
    target_type: SLATargetType = "check_success"
    objective: float = Field(0.99, gt=0, le=1)
    window: SLAWindow = "rolling_30d"
    enabled: bool = True


class SLAUpdate(BaseModel):
    name: str | None = None
    target_type: SLATargetType | None = None
    objective: float | None = Field(default=None, gt=0, le=1)
    window: SLAWindow | None = None
    enabled: bool | None = None


class SLAEvaluationOut(ORMModel):
    id: int
    evaluated_at: datetime
    window_start: datetime
    window_end: datetime
    attainment: float
    budget_consumed: float
    good: int
    bad: int
    breached: bool
    mttr_seconds: int | None = None
    mttd_seconds: int | None = None


class SLAOut(BaseModel):
    id: int
    name: str
    scope: SLAScope
    scope_id: int
    target_type: SLATargetType
    objective: float
    window: SLAWindow
    enabled: bool
    created_at: datetime
    scope_label: str = ""  # dataset/check display name
    dataset_id: int | None = None  # the dataset this SLA rolls up to
    latest: SLAEvaluationOut | None = None  # most recent rollup


class SLADetailOut(SLAOut):
    evaluations: list[SLAEvaluationOut] = []  # recent rollups, oldest→newest (burn-down)


class ReliabilityOut(BaseModel):
    total: int
    breached: int
    slas: list[SLAOut]


# ---- knowledge ----
class KnowledgeIn(BaseModel):
    business_context: str = ""
    known_issues: str = ""
    importance: Literal["low", "medium", "high", "critical"] = "medium"
    owner: str = ""
    freshness_sla_hours: int | None = None
    pii_columns: list[str] = []
    notes: str = ""


class KnowledgeOut(KnowledgeIn):
    model_config = ConfigDict(from_attributes=True)
    dataset_id: int
    updated_at: datetime | None = None


# ---- checks ----
class CheckIn(BaseModel):
    dataset_id: int
    name: str = ""
    check_type: str
    column_name: str | None = None
    params: dict[str, Any] = {}
    severity: Severity = "error"
    rationale: str = ""
    schedule_kind: Literal["interval", "cron"] | None = "interval"
    schedule_expr: str | None = "1440"
    status: CheckStatus = "active"


class CheckUpdate(BaseModel):
    name: str | None = None
    column_name: str | None = None
    params: dict[str, Any] | None = None
    severity: Severity | None = None
    rationale: str | None = None
    schedule_kind: Literal["interval", "cron"] | None = None
    schedule_expr: str | None = None
    status: CheckStatus | None = None


class CheckOut(ORMModel):
    id: int
    dataset_id: int
    dataset_name: str = ""
    name: str
    check_type: str
    column_name: str | None
    params: dict[str, Any]
    severity: Severity
    status: CheckStatus
    origin: str
    rationale: str
    schedule_kind: str | None
    schedule_expr: str | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    created_at: datetime


# ---- monitor packs (issue #115) ----
MonitorKind = Literal["freshness", "volume", "schema", "drift"]


class MonitorPackUpdate(BaseModel):
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class MonitorPackSkipped(BaseModel):
    kind: MonitorKind | str
    column_name: str | None = None
    code: str = ""
    reason: str


class MonitorPackReconciliationOut(BaseModel):
    status: str
    profile_id: int | None = None
    created: int = 0
    updated: int = 0
    disabled: int = 0
    skipped: list[MonitorPackSkipped] = []
    message: str = ""


class MonitorPackOut(ORMModel):
    id: int
    dataset_id: int
    enabled: bool
    config: dict[str, Any]
    status: str
    last_error: str
    created_at: datetime
    updated_at: datetime
    last_reconciled_at: datetime | None
    reconciliation: MonitorPackReconciliationOut | None = None
    managed_checks: list[CheckOut] = []


class GenerateChecksIn(BaseModel):
    dataset_id: int
    use_llm: bool = True  # falls back to heuristics when LLM unavailable
    explore: bool = False  # run the progressive exploration agent first (LLM only)


class GenerateChecksOut(BaseModel):
    created: int
    skipped_duplicates: int
    mode: Literal["llm", "heuristic"]
    explored: bool = False
    checks: list[CheckOut]


class CheckTypeInfo(BaseModel):
    key: str
    label: str
    description: str
    needs_column: bool
    params: list[dict[str, Any]]


# ---- runs ----
class RunOut(ORMModel):
    id: int
    check_id: int
    check_name: str = ""
    check_type: str = ""
    dataset_id: int
    dataset_name: str = ""
    started_at: datetime
    finished_at: datetime | None
    status: RunStatus
    violation_count: int
    rows_evaluated: int | None
    metrics: dict[str, Any]
    error_message: str
    triggered_by: str
    exception_count: int = 0


# ---- exceptions (workbench: #55 identity, #56 triage workflow, #57 API v2) ----
class ExceptionOut(ORMModel):
    id: int
    run_id: int
    check_id: int
    check_name: str = ""
    check_type: str = ""
    check_severity: Severity = "error"  # denormalized from the check for the triage table
    column_name: str | None = None
    dataset_id: int
    dataset_name: str = ""
    row_data: dict[str, Any]
    reason: str
    outlier_score: float | None
    status: ExceptionStatus
    note: str
    marked_by: str | None = None
    marked_at: datetime | None
    created_at: datetime
    # --- identity & recurrence (#55) ---
    fingerprint: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_run_id: int | None = None
    occurrence_count: int = 1
    # --- triage workflow (#56) ---
    assigned_to_id: int | None = None
    assigned_to: str | None = None  # resolved display name ("(inactive)" suffix when deactivated)


class TriageIn(BaseModel):
    # Bulk cap (#56): bounds transaction size, payload, and event-write amplification.
    ids: list[int] = Field(max_length=1000)
    status: ExceptionStatus | None = None  # optional: comment/assign without a status change
    note: str = ""
    assigned_to_id: int | None = None
    clear_assignee: bool = False


class ExceptionEventOut(ORMModel):
    id: int
    exception_id: int
    kind: str  # status | comment | assign | system
    from_status: str = ""
    to_status: str = ""
    comment: str = ""
    user: str | None = None  # resolved display name; None = system action
    created_at: datetime


class CommentIn(BaseModel):
    comment: str = Field(min_length=1)


# ---- exceptions API v2 (#57) ----
class FacetEntry(BaseModel):
    id: int
    name: str
    count: int


class ExceptionFacets(BaseModel):
    status: dict[str, int]
    severity: dict[str, int]
    check_type: dict[str, int]
    datasets: list[FacetEntry]  # {id, name, count}, ordered by count desc, top 20
    total: int


class ExceptionPage(BaseModel):
    items: list[ExceptionOut]
    total: int
    limit: int
    offset: int


# ---- RCA ----
class RcaStartIn(BaseModel):
    dataset_id: int | None = None
    check_run_id: int | None = None
    question: str = ""


class RcaOut(ORMModel):
    id: int
    dataset_id: int
    dataset_name: str = ""
    check_run_id: int | None
    question: str
    status: str
    report_md: str
    root_cause_summary: str
    transcript: list[Any]
    model: str
    created_at: datetime
    finished_at: datetime | None


# ---- assistant chat ----
class ChatSessionCreateIn(BaseModel):
    title: str = Field(default="", max_length=300)


class ChatMessageOut(ORMModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    steps: list[Any]
    created_at: datetime


class ChatSessionOut(ORMModel):
    id: int
    title: str
    model: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ChatSessionDetailOut(ChatSessionOut):
    messages: list[ChatMessageOut] = []


# ---- workbench / queries ----
class QueryRunIn(BaseModel):
    connection_id: int
    sql: str
    limit: int = Field(default=200, ge=1, le=2000)


class QueryRunOut(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int


class SchemaTable(BaseModel):
    schema_name: str | None = None
    table_name: str
    kind: Literal["table", "view"] = "table"
    columns: list[ColumnInfo] = []


class DdlOut(BaseModel):
    table_name: str
    ddl: str
    source: Literal["database", "synthesized"]


class SuggestIn(BaseModel):
    connection_id: int | None = None
    dataset_id: int | None = None
    check_id: int | None = None
    run_id: int | None = None
    exception_id: int | None = None
    goal: str = ""


class Suggestion(BaseModel):
    title: str
    sql: str
    rationale: str = ""


class SuggestOut(BaseModel):
    mode: Literal["llm", "heuristic"]
    connection_id: int
    suggestions: list[Suggestion]


class ConnectionHealth(BaseModel):
    id: int
    name: str
    ok: bool
    message: str
    latency_ms: int | None = None


# ---- saved queries (team snippet library) ----
class SavedQueryIn(BaseModel):
    connection_id: int
    dataset_id: int | None = None  # set => pin to this dataset
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    sql: str = Field(min_length=1)
    tags: list[str] = []


class SavedQueryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    sql: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = None
    dataset_id: int | None = None  # null leaves the pin unchanged; use unpin=True to clear
    unpin: bool = False  # explicitly clear the dataset pin


class SavedQueryOut(ORMModel):
    id: int
    connection_id: int
    dataset_id: int | None
    name: str
    description: str
    sql: str
    tags: list[str]
    created_by_id: int | None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    last_run_at: datetime | None


# ---- MCP servers ----
class McpServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8)
    auth_token: str = ""
    description: str = ""
    enabled: bool = True


class McpServerUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    auth_token: str | None = None  # empty string clears; None leaves unchanged
    description: str | None = None
    enabled: bool | None = None


class McpServerOut(ORMModel):
    id: int
    name: str
    url: str
    description: str
    enabled: bool
    has_token: bool = False
    created_at: datetime


# ---- notification rules (issue #27) ----
NotifyChannel = Literal["slack", "email"]


class NotificationRuleIn(BaseModel):
    dataset_id: int | None = None  # None = all datasets
    min_severity: Severity = "error"
    channel: NotifyChannel
    target: str = ""  # webhook URL or comma-separated emails ("" = global Slack default)
    on_error_runs: bool = True
    enabled: bool = True


class NotificationRuleUpdate(BaseModel):
    dataset_id: int | None = None
    min_severity: Severity | None = None
    channel: NotifyChannel | None = None
    target: str | None = None
    on_error_runs: bool | None = None
    enabled: bool | None = None


class NotificationRuleOut(ORMModel):
    id: int
    dataset_id: int | None
    dataset_name: str = ""  # "" when dataset_id is None (all datasets)
    min_severity: Severity
    channel: NotifyChannel
    target: str
    on_error_runs: bool
    enabled: bool
    created_at: datetime


# ---- audit log (issue #30) ----
class AuditEntryOut(ORMModel):
    id: int
    user_id: int | None
    user: str | None = None  # resolved display name (name or email), None = system/anonymous
    action: str
    entity_type: str
    entity_id: int | None
    detail: dict[str, Any]
    request_id: str
    client_ip: str
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditEntryOut]
    total: int
    limit: int
    offset: int


# ---- ad-hoc dashboards ----
class PanelViz(BaseModel):
    type: Literal["number", "bar", "line", "area", "pie", "table"] = "table"
    x: str | None = None
    y: str | None = None


class PanelSpec(BaseModel):
    title: str
    description: str = ""
    sql: str
    viz: PanelViz = PanelViz()


class PanelData(PanelSpec):
    columns: list[str] = []
    rows: list[list[Any]] = []
    error: str | None = None
    elapsed_ms: int = 0


class GenerateDashboardIn(BaseModel):
    dataset_id: int
    focus: str = ""


class AdhocDashboardMeta(ORMModel):
    id: int
    dataset_id: int
    title: str
    focus: str
    origin: str
    created_at: datetime
    last_refreshed_at: datetime | None
    panel_count: int = 0


class AdhocDashboardOut(AdhocDashboardMeta):
    panels: list[PanelData] = []


# ---- dashboard ----
class TrendPoint(BaseModel):
    day: str
    passed: int
    warned: int
    failed: int
    errored: int


class DashboardOut(BaseModel):
    datasets: int
    active_checks: int
    proposed_checks: int
    runs_24h: int
    failing_checks: int
    open_exceptions: int
    llm_enabled: bool
    pass_rate_7d: float | None
    trend: list[TrendPoint]
    recent_runs: list[RunOut]
    worst_datasets: list[DatasetOut]


# --- DDL & lineage (issue #51) ---
class DatasetDdlOut(BaseModel):
    """Source definition of a dataset's table/view.

    (Named DatasetDdlOut because the workbench already uses DdlOut for
    GET /connections/{id}/ddl with a different shape.)
    """

    dataset_id: int
    ddl: str
    source: Literal["database", "synthesized"]
    kind: Literal["table", "view"] = "table"


class LineageNode(BaseModel):
    id: str  # "schema.table" when schema_name is set, else "table" (lowercased)
    schema_name: str | None = None
    table_name: str
    kind: Literal["table", "view"] = "table"
    dataset_id: int | None = None  # null for unregistered/external tables
    health: Literal["pass", "warn", "fail", "unknown"] = "unknown"
    failing_checks: int = 0
    open_exceptions: int = 0


class LineageEdge(BaseModel):
    source: str  # upstream node id — data flows source -> target
    target: str  # the view selecting from source


class LineageGraph(BaseModel):
    nodes: list[LineageNode] = []
    edges: list[LineageEdge] = []
    parse_errors: int = 0  # view definitions sqlglot could not parse
    truncated: bool = False  # graph exceeded the node cap and was cut off


# ---- custom dashboards (issue #67) -----------------------------------------
# The widget JSON contract below is authoritative for BOTH this backend and
# frontend/src/api/types.ts — keep the two mirrored exactly.

# Allowed keys for metric/exceptions widget `params`. These mirror the
# GET /exceptions query contract from #57 (rich filters); the frontend resolves
# the widget by calling that endpoint with these stored params and reading the
# `total` of its envelope — so a widget's count is ALWAYS the same number the
# triage queue would show for the same filters. Never build a parallel count.
EXCEPTION_PARAM_KEYS = frozenset(
    {
        "status",
        "severity",
        "check_type",
        "dataset_id",
        "check_id",
        "run_id",
        "assignee",
        "recurrence",
        "seen_since",
        "q",
        "sort",
    }
)

MAX_WIDGETS = 12  # quota policy; per-tenant quotas (multi-tenancy track) hook here
MAX_DATASET_IDS = 20  # checks-widget cap; same quota posture
MAX_NOTE_CHARS = 5_000
SNAPSHOT_ROW_CAP = 200  # rows persisted per sql-widget snapshot (quota policy)

Visibility = Literal["private", "team"]
WidgetSpan = Literal[1, 2]


def _validate_param_keys(params: dict[str, str]) -> dict[str, str]:
    """Reject params keys not in the #57 allowlist; coerce values to strings."""
    bad = set(params) - EXCEPTION_PARAM_KEYS
    if bad:
        raise ValueError(
            f"Unknown filter param(s): {', '.join(sorted(bad))}. "
            f"Allowed: {', '.join(sorted(EXCEPTION_PARAM_KEYS))}"
        )
    return {k: str(v) for k, v in params.items()}


class WidgetBase(BaseModel):
    id: str = Field(min_length=1, max_length=64)  # client-generated uuid
    title: str = Field(min_length=1, max_length=200)
    span: WidgetSpan = 1


class MetricWidgetConfig(BaseModel):
    params: dict[str, str] = {}
    warn_at: float | None = None  # tone thresholds on the count (null = never)
    danger_at: float | None = None

    @field_validator("params")
    @classmethod
    def _params_ok(cls, v: dict[str, str]) -> dict[str, str]:
        return _validate_param_keys(v)


class MetricWidget(WidgetBase):
    type: Literal["metric"] = "metric"
    config: MetricWidgetConfig


class ExceptionsWidgetConfig(BaseModel):
    params: dict[str, str] = {}
    limit: int = Field(default=5, ge=1, le=10)  # clamped 1..10

    @field_validator("params")
    @classmethod
    def _params_ok(cls, v: dict[str, str]) -> dict[str, str]:
        return _validate_param_keys(v)


class ExceptionsWidget(WidgetBase):
    type: Literal["exceptions"] = "exceptions"
    config: ExceptionsWidgetConfig


class ChecksWidgetConfig(BaseModel):
    dataset_ids: list[int] = []
    only_failing: bool = False

    @field_validator("dataset_ids")
    @classmethod
    def _cap_ids(cls, v: list[int]) -> list[int]:
        if len(v) > MAX_DATASET_IDS:
            raise ValueError(f"At most {MAX_DATASET_IDS} datasets per checks widget")
        return v


class ChecksWidget(WidgetBase):
    type: Literal["checks"] = "checks"
    config: ChecksWidgetConfig


class WidgetSnapshot(BaseModel):
    """Server-executed sql-widget result. WRITTEN ONLY BY THE SERVER (the /refresh
    endpoint); client-sent snapshots are stripped on every write. `refreshed_at`
    is mandatory so the UI can label freshness honestly (data was captured with
    the refresher's authority, not the viewer's — same posture as ad-hoc boards)."""

    columns: list[str] = []
    rows: list[list[Any]] = []
    refreshed_at: datetime
    error: str | None = None
    elapsed_ms: int = 0


class SqlWidgetConfig(BaseModel):
    connection_id: int
    sql: str
    viz: PanelViz = PanelViz()


class SqlWidget(WidgetBase):
    type: Literal["sql"] = "sql"
    config: SqlWidgetConfig
    # Optional on input (and ignored — server-owned); populated on output.
    snapshot: WidgetSnapshot | None = None


class NoteWidgetConfig(BaseModel):
    markdown: str = Field(default="", max_length=MAX_NOTE_CHARS)


class NoteWidget(WidgetBase):
    type: Literal["note"] = "note"
    config: NoteWidgetConfig


Widget = Annotated[
    MetricWidget | ExceptionsWidget | ChecksWidget | SqlWidget | NoteWidget,
    Field(discriminator="type"),
]


class DashboardLayout(BaseModel):
    version: Literal[1] = 1  # mandatory artifact version (epic standard #7)
    widgets: list[Widget] = []

    @field_validator("widgets")
    @classmethod
    def _cap_and_dedupe(cls, v: list[Widget]) -> list[Widget]:
        if len(v) > MAX_WIDGETS:
            raise ValueError(f"A dashboard may have at most {MAX_WIDGETS} widgets")
        ids = [w.id for w in v]
        if len(set(ids)) != len(ids):
            raise ValueError("Widget ids must be unique within a dashboard")
        return v


class CustomDashboardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    visibility: Visibility = "private"
    layout: DashboardLayout = DashboardLayout()


class CustomDashboardUpdate(BaseModel):
    """Full-layout replace (the UI always sends the whole layout — there is no
    widget-level PATCH). All fields optional so partial metadata edits work.
    `owner_id` is honored for admins only (owner reassignment on offboarding)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    visibility: Visibility | None = None
    layout: DashboardLayout | None = None
    owner_id: int | None = None  # admin-only; ignored for non-admins in the router


class CustomDashboardMeta(ORMModel):
    id: int
    name: str
    description: str
    owner_id: int
    owner_name: str = ""  # joined display field (serialize.py pattern)
    owner_active: bool = True  # offboarding: owner shown "(inactive)" in the UI
    visibility: Visibility
    widget_count: int = 0
    created_at: datetime
    updated_at: datetime


class CustomDashboardOut(CustomDashboardMeta):
    layout: DashboardLayout = DashboardLayout()
    can_edit: bool = False  # owner or admin — drives the builder Edit toggle


# --- global search (issue #43) ---
class SearchHit(BaseModel):
    type: Literal["dataset", "check", "connection", "saved_query"]
    id: int
    title: str  # e.g. "shop.orders" / check name / connection name
    subtitle: str  # e.g. connection name / dataset name / kind
    url: str  # SPA path the frontend navigates to, e.g. "/datasets/12"


class SearchOut(BaseModel):
    hits: list[SearchHit] = []
