"""Pydantic request/response schemas. Mirror changes into frontend/src/api/types.ts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
