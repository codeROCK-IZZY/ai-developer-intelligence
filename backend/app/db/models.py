"""SQLAlchemy ORM models. The schema is created by the explicit-SQL Alembic migration;
``tests/test_schema.py`` checks that models and migration agree."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 384

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class Base(DeclarativeBase):
    metadata = metadata


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=sql_text("gen_random_uuid()")
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _fk_uuid(target: str, ondelete: str = "CASCADE", nullable: bool = False, index: bool = False) -> Mapped[Any]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index)


def _fk_snapshot() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False, index=True)


def _fk_big(target: str, nullable: bool = False, index: bool = False) -> Mapped[Any]:
    return mapped_column(BigInteger, ForeignKey(target, ondelete="CASCADE"), nullable=nullable, index=index)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _fk_uuid("users.id", index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Workspace(Base):
    """A team space. Membership (with a role) is what grants access to its repositories."""

    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("name", name="uq_workspaces_name"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by: Mapped[uuid.UUID | None] = _fk_uuid("users.id", ondelete="SET NULL", nullable=True)
    created_at: Mapped[datetime] = _created()


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # owner | editor | reader
    created_at: Mapped[datetime] = _created()


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_repositories_workspace_name"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk_uuid("workspaces.id", index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_url: Mapped[str | None] = mapped_column(String(500))
    default_ref: Mapped[str | None] = mapped_column(String(200))
    latest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = _fk_uuid("users.id", ondelete="SET NULL", nullable=True)
    created_at: Mapped[datetime] = _created()


class Snapshot(Base):
    """One imported version of a repository (a ZIP upload, or a GitHub ref at a point in time)."""

    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("repository_id", "seq", name="uq_snapshots_repository_seq"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    repository_id: Mapped[uuid.UUID] = _fk_uuid("repositories.id")
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # upload | github
    ref: Mapped[str | None] = mapped_column(String(200))
    file_key: Mapped[str | None] = mapped_column(String(500))
    archive_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    stage: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    skipped: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = _fk_uuid("users.id", ondelete="SET NULL", nullable=True)
    created_at: Mapped[datetime] = _created()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CodeFile(Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("snapshot_id", "path", name="uq_files_snapshot_path"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    loc: Mapped[int] = mapped_column(Integer, nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    health: Mapped[int] = mapped_column(Integer, nullable=False)
    centrality: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    fan_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    fan_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    content: Mapped[str] = mapped_column(Text, nullable=False)


class CodeSymbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (Index("ix_symbols_snapshot_name", "snapshot_id", func.lower(sql_text("name"))),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = _fk_snapshot()
    file_id: Mapped[int] = _fk_big("files.id", index=True)
    parent_id: Mapped[int | None] = _fk_big("symbols.id", nullable=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    qualified_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    docstring: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    exported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decorators: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'[]'::jsonb"))
    params: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    complexity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    max_nesting: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    loc: Mapped[int] = mapped_column(Integer, nullable=False)
    body_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    fan_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    fan_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))


class Ref(Base):
    """A call site. ``callee_id`` is NULL when the call could not be resolved to repository code."""

    __tablename__ = "refs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = _fk_snapshot()
    file_id: Mapped[int] = _fk_big("files.id")
    caller_id: Mapped[int | None] = _fk_big("symbols.id", nullable=True, index=True)
    callee_id: Mapped[int | None] = _fk_big("symbols.id", nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    receiver: Mapped[str | None] = mapped_column(String(100))
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(20))


class ImportRow(Base):
    __tablename__ = "imports"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = _fk_snapshot()
    file_id: Mapped[int] = _fk_big("files.id")
    module: Mapped[str] = mapped_column(String(500), nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    package: Mapped[str | None] = mapped_column(String(200))
    target_file_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'[]'::jsonb"))


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = _fk_snapshot()
    file_id: Mapped[int] = _fk_big("files.id")
    symbol_id: Mapped[int | None] = _fk_big("symbols.id", nullable=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)


class FindingRow(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = _fk_snapshot()
    file_id: Mapped[int] = _fk_big("files.id")
    symbol_id: Mapped[int | None] = _fk_big("symbols.id", nullable=True)
    rule: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)


class Duplicate(Base):
    __tablename__ = "duplicates"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = _fk_snapshot()
    symbol_a: Mapped[int] = _fk_big("symbols.id")
    symbol_b: Mapped[int] = _fk_big("symbols.id")
    similarity: Mapped[float] = mapped_column(Float, nullable=False)


class Question(Base):
    """A question asked about code, with its answer and citations (the Q&A history)."""

    __tablename__ = "questions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _fk_uuid("users.id", index=True)
    repository_id: Mapped[uuid.UUID | None] = _fk_uuid("repositories.id", ondelete="SET NULL", nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created()


class SearchHistory(Base):
    __tablename__ = "search_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = _fk_uuid("users.id", index=True)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    reranked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    results: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = _fk_uuid("users.id", ondelete="SET NULL", nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
