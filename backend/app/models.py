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
    exceptions: Mapped[list["ExceptionRecord"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class ExceptionRecord(Base):
    __tablename__ = "exception_records"
    __table_args__ = (Index("ix_exc_dataset_status", "dataset_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id"), index=True)
    check_id: Mapped[int] = mapped_column(ForeignKey("checks.id"), index=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    row_data: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    outlier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # open -> acknowledged | expected | resolved | muted
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    marked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    marked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    run: Mapped[CheckRun] = relationship(back_populates="exceptions")


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
