"""SQLAlchemy models + engine for the durable task/result store (PostgreSQL).

`tasks` is the source of truth for task status + doc_meta; `chunks` holds the
parsed chunks for each task. The Redis cache may expire its status keys, but
these rows survive — that is the durability fallback for polling.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class TaskRow(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    total_pages: Mapped[int | None] = mapped_column(nullable=True)
    title: Mapped[str | None] = mapped_column(Text)
    warning: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ChunkRow(Base):
    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_task_seq", "task_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column()
    chunk_id: Mapped[str] = mapped_column(String(256))
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int] = mapped_column()
    heading: Mapped[str] = mapped_column(Text)
    chunk_type: Mapped[str] = mapped_column(String(16))


def create_engine_from_dsn(dsn: str):
    """Create a sync engine (psycopg2). `pool_pre_ping` guards stale connections."""
    return create_engine(dsn, pool_pre_ping=True, future=True)


def init_db(engine) -> None:
    """Create tables if they don't exist (idempotent). Alembic is the follow-up
    once the schema stabilizes."""
    Base.metadata.create_all(engine)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
