"""SQLAlchemy 2.0 ORM models for the app metadata database.

Status/enum-ish fields are plain strings validated at the schema layer to keep
migrations trivial. JSON columns use sa.JSON (TEXT on SQLite, JSON on Postgres).
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # store naive UTC


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # viewer | editor | admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # sqlite | duckdb | postgresql
    dsn: Mapped[str] = mapped_column(Text)  # plaintext for now — see issue #24 (encrypt at rest)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    datasets: Mapped[list["Dataset"]] = relationship(back_populates="connection", cascade="all, delete-orphan")


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("connection_id", "schema_name", "table_name", name="uq_dataset_table"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("connections.id"))
    schema_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    table_name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255), default="")
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_profiled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exploration: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # LLM explorer insights
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    connection: Mapped[Connection] = relationship(back_populates="datasets")
    checks: Mapped[list["Check"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    profiles: Mapped[list["Profile"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    knowledge: Mapped["TableKnowledge | None"] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", uselist=False
    )


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    sampled_rows: Mapped[int] = mapped_column(Integer, default=0)
    columns: Mapped[list] = mapped_column(JSON, default=list)  # list[ColumnProfile dict]
    table_facts: Mapped[dict] = mapped_column(JSON, default=dict)  # pk candidates, temporal cols, ...

    dataset: Mapped[Dataset] = relationship(back_populates="profiles")


class SchemaSnapshot(Base):
    """Point-in-time column schema of a dataset (issue #101).

    Captured (deduped by ``fingerprint``) on each profile run and on each
    ``schema_change`` check run. Powers the schema-history timeline and the
    ``schema_change`` check's pinned baseline. ``source``: profile | check |
    baseline; ``is_baseline`` marks the single pinned baseline used by
    ``baseline=pinned`` checks.
    """

    __tablename__ = "schema_snapshots"
    __table_args__ = (Index("ix_schema_snap_dataset", "dataset_id", "captured_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(20), default="profile")  # profile | check | baseline
    columns: Mapped[list] = mapped_column(JSON, default=list)  # [{name, dtype, nullable, ordinal}]
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)


class TableKnowledge(Base):
    __tablename__ = "table_knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), unique=True)
    business_context: Mapped[str] = mapped_column(Text, default="")
    known_issues: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[str] = mapped_column(String(20), default="medium")  # low|medium|high|critical
    owner: Mapped[str] = mapped_column(String(255), default="")
    freshness_sla_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pii_columns: Mapped[list] = mapped_column(JSON, default=list)  # column names redacted in LLM prompts
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    dataset: Mapped[Dataset] = relationship(back_populates="knowledge")


class Check(Base):
    __tablename__ = "checks"
    __table_args__ = (Index("ix_checks_due", "status", "next_run_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    check_type: Mapped[str] = mapped_column(String(50))
    column_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(10), default="error")  # info | warn | error
    # proposed -> active -> disabled/archived
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    origin: Mapped[str] = mapped_column(String(20), default="manual")  # heuristic | llm | manual | system
    rationale: Mapped[str] = mapped_column(Text, default="")
    schedule_kind: Mapped[str | None] = mapped_column(String(10), nullable=True)  # interval | cron
    schedule_expr: Mapped[str | None] = mapped_column(String(100), nullable=True)  # minutes or cron string
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    dataset: Mapped[Dataset] = relationship(back_populates="checks")
    runs: Mapped[list["CheckRun"]] = relationship(back_populates="check", cascade="all, delete-orphan")


class CheckRun(Base):
    __tablename__ = "check_runs"
    __table_args__ = (Index("ix_runs_check_started", "check_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey("checks.id"))
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="error")  # pass | warn | fail | error
    violation_count: Mapped[int] = mapped_column(Integer, default=0)
    rows_evaluated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str] = mapped_column(Text, default="")
    triggered_by: Mapped[str] = mapped_column(String(10), default="manual")  # manual | schedule

    check: Mapped[Check] = relationship(back_populates="runs")
    # ExceptionRecord has two FKs to check_runs (run_id = first capture,
    # last_run_id = most recent sighting, #55); this collection tracks run_id.
    exceptions: Mapped[list["ExceptionRecord"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        foreign_keys="ExceptionRecord.run_id",
    )


class ExceptionRecord(Base):
    __tablename__ = "exception_records"
    __table_args__ = (
        Index("ix_exc_dataset_status", "dataset_id", "status"),
        # --- exceptions workbench (#55): identity + recurrence hot path ---
        Index("ix_exc_check_fingerprint", "check_id", "fingerprint"),
        # --- exceptions workbench (#57): faceted-search "recently seen" filter ---
        Index("ix_exc_status_last_seen", "status", "last_seen_at"),
        # --- exceptions workbench (#56): "assigned to me" queues ---
        Index("ix_exc_assigned", "assigned_to_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id"), index=True)
    check_id: Mapped[int] = mapped_column(ForeignKey("checks.id"), index=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    row_data: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    outlier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # open -> acknowledged | expected | resolved | muted
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    # NOTE: `note` is a *denormalized* "latest comment" convenience. The full,
    # append-only history lives in ExceptionEvent (#56) — do not "fix" the
    # duplication by removing this field; existing UI reads it as the last note.
    note: Mapped[str] = mapped_column(Text, default="")
    marked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    marked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # --- exceptions workbench: identity & recurrence (#55) ---
    # Stable per-row identity so reconciliation updates instead of re-inserting.
    # `run_id` keeps meaning "run that first captured this row"; `last_run_id`
    # is the most recent run that saw it. fingerprint is NULL for historical rows
    # (the runner only matches non-NULL fingerprints).
    # TODO: retention policy for resolved/muted records — see #30 retention pattern
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("check_runs.id"), nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)

    # --- exceptions workbench: triage workflow (#56) ---
    # Existing assignments to deactivated users are preserved (display gets an
    # "(inactive)" suffix); only NEW assignments to inactive users are blocked.
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    run: Mapped[CheckRun] = relationship(
        back_populates="exceptions", foreign_keys=[run_id]
    )
    # Append-only activity trail. Cascade so deleting a record cleans up events.
    events: Mapped[list["ExceptionEvent"]] = relationship(
        cascade="all, delete-orphan", order_by="ExceptionEvent.id"
    )


class ExceptionEvent(Base):
    """Append-only triage activity for an exception (#56).

    Events are the analyst team's institutional memory and (eventually)
    compliance evidence. There are intentionally NO update/delete paths —
    this table is write-once. `kind`: status | comment | assign | system
    (user_id is None for machine actions: auto-resolve, regression reopen).
    """

    __tablename__ = "exception_events"
    __table_args__ = (Index("ix_exc_events_exc", "exception_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    exception_id: Mapped[int] = mapped_column(ForeignKey("exception_records.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # None = system
    kind: Mapped[str] = mapped_column(String(20))  # status | comment | assign | system
    from_status: Mapped[str] = mapped_column(String(20), default="")
    to_status: Mapped[str] = mapped_column(String(20), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class McpServer(Base):
    """Admin-registered MCP servers passed to LLM calls via the Claude MCP
    connector, giving agents code context (dbt models, repos, docs)."""

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    url: Mapped[str] = mapped_column(Text)
    auth_token: Mapped[str] = mapped_column(Text, default="")  # write-only; masked in API responses
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AdhocDashboard(Base):
    """A generated investigation dashboard: panels of {sql, viz} re-executed on open."""

    __tablename__ = "adhoc_dashboards"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    focus: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(20), default="heuristic")  # llm | heuristic
    spec: Mapped[dict] = mapped_column(JSON, default=dict)  # {panels: [PanelSpec]}
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CustomDashboard(Base):
    """A user-composed, cross-dataset dashboard (issue #67). The sibling of the
    LLM/heuristic per-dataset AdhocDashboard: here the analyst hand-picks widgets
    ("my morning screen"), shares with the team, and can set it as their landing
    page. Widgets live inside ``layout`` JSON — they have no independent identity,
    so no separate widget table (a 12-widget cap makes joins pointless). The SQL
    widgets' server-executed results are persisted inside that same JSON as
    ``snapshot`` blobs (see schemas.WidgetSnapshot); every other widget type
    resolves live, client-side, through the existing read APIs.
    """

    __tablename__ = "custom_dashboards"
    __table_args__ = (Index("ix_customdash_owner", "owner_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    visibility: Mapped[str] = mapped_column(String(10), default="private")  # private | team
    # {"version": 1, "widgets": [...]} — version is mandatory (epic standard #7).
    layout: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ChatSession(Base):
    """An assistant conversation. Messages persist; the provider-native LLM
    history is kept in process memory and rehydrated from messages on demand."""

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    # Ordered activity for assistant turns: [{type: text|sql|result|chart|error, ...}]
    steps: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class SavedQuery(Base):
    """A workbench query saved to the shared team library (issue #41). Optionally
    pinned to a dataset (dataset_id set) so it surfaces where investigations start.
    SQL is validated through guard_sql() at save time, so the library stays runnable."""

    __tablename__ = "saved_queries"
    __table_args__ = (Index("ix_savedq_conn", "connection_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("connections.id"))
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id"), nullable=True
    )  # set => pinned to that dataset
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    sql: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)  # list[str]
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NotificationRule(Base):
    """Routing rule for failure/recovery notifications (issue #27).

    Firing decision lives in core/runner.py (transition-based); this row only
    decides *where* a fired event goes. ``dataset_id is None`` matches every
    dataset; ``min_severity`` gates on the check's severity; ``target`` is the
    Slack webhook URL or comma-separated emails (empty Slack target falls back
    to the global ``notify_slack_webhook_url`` setting)."""

    __tablename__ = "notification_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id"), nullable=True, index=True
    )  # None = all datasets
    min_severity: Mapped[str] = mapped_column(String(10), default="error")  # info | warn | error
    channel: Mapped[str] = mapped_column(String(10))  # slack | email
    target: Mapped[str] = mapped_column(Text, default="")  # webhook URL or comma-separated emails
    on_error_runs: Mapped[bool] = mapped_column(Boolean, default=True)  # also fire on status == "error"
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEntry(Base):
    """Append-only audit trail of security/config-relevant actions (issue #30).

    Distinct from ``ExceptionEvent`` (#56): that is the per-exception user-facing
    timeline; this is the global admin/compliance surface. NEVER store secrets,
    DSNs, password hashes, or source row data in ``detail``.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )  # None = system/anonymous (e.g. failed login)
    action: Mapped[str] = mapped_column(String(50))  # e.g. "login.success", "check.update"
    entity_type: Mapped[str] = mapped_column(String(30))  # user|connection|dataset|check|exception|...
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # diffs/params — NEVER secrets or row data
    request_id: Mapped[str] = mapped_column(String(16), default="")  # joins to request logs
    client_ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SLADefinition(Base):
    """A reliability target for a dataset or a single check (issue #102).

    Attainment is measured run-over-run from CheckRun history: a window's good
    runs (pass/warn) over total (pass/warn/fail/error) for the SLA's target
    checks, compared to ``objective``. ``scope_id`` is a dataset_id or check_id
    (polymorphic, intentionally not a hard FK). ``target_type`` selects which of
    a dataset's checks count (freshness | volume | check_success = all).
    """

    __tablename__ = "sla_definitions"
    __table_args__ = (Index("ix_sla_enabled", "enabled"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    scope: Mapped[str] = mapped_column(String(10), default="dataset")  # dataset | check
    scope_id: Mapped[int] = mapped_column(Integer)  # dataset_id or check_id
    target_type: Mapped[str] = mapped_column(String(20), default="check_success")  # freshness|volume|check_success
    objective: Mapped[float] = mapped_column(Float, default=0.99)  # target attainment fraction (0,1]
    window: Mapped[str] = mapped_column(String(20), default="rolling_30d")  # rolling_7d | rolling_30d
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    evaluations: Mapped[list["SLAEvaluation"]] = relationship(
        back_populates="sla", cascade="all, delete-orphan", order_by="SLAEvaluation.id"
    )


class SLAEvaluation(Base):
    """A periodic SLA rollup over the window (issue #102): attainment, error
    budget consumed, good/bad run counts, breach flag, and MTTR."""

    __tablename__ = "sla_evaluations"
    __table_args__ = (Index("ix_slaeval_sla", "sla_id", "evaluated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sla_id: Mapped[int] = mapped_column(ForeignKey("sla_definitions.id"), index=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    window_start: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    window_end: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    attainment: Mapped[float] = mapped_column(Float, default=1.0)
    budget_consumed: Mapped[float] = mapped_column(Float, default=0.0)
    good: Mapped[int] = mapped_column(Integer, default=0)
    bad: Mapped[int] = mapped_column(Integer, default=0)
    breached: Mapped[bool] = mapped_column(Boolean, default=False)
    mttr_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mttd_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    sla: Mapped[SLADefinition] = relationship(back_populates="evaluations")


class RcaSession(Base):
    __tablename__ = "rca_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    check_run_id: Mapped[int | None] = mapped_column(ForeignKey("check_runs.id"), nullable=True)
    question: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | complete | failed
    report_md: Mapped[str] = mapped_column(Text, default="")
    root_cause_summary: Mapped[str] = mapped_column(Text, default="")
    transcript: Mapped[list] = mapped_column(JSON, default=list)  # [{role, content}] steps incl. SQL + results
    model: Mapped[str] = mapped_column(String(100), default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
