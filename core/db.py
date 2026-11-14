"""
SQLAlchemy models on SQLite. No server, no Docker.

Embeddings do NOT live here — they go to flat binary files handled by core/vectors.py,
keyed by chunk id. SQLite holds text + metadata + structured facts.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import JSON, Date, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from core.config import settings


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    doc_type: Mapped[str] = mapped_column(String(32), index=True)  # results|concall|annual_report|shareholding|announcement
    fiscal_period: Mapped[Optional[str]] = mapped_column(String(16))
    filed_on: Mapped[Optional[date]] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text, unique=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    n_pages: Mapped[int] = mapped_column(Integer, default=0)
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    section: Mapped[Optional[str]] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    document: Mapped[Document] = relationship(back_populates="chunks")


class Fact(Base):
    """Structured facts for the red-flag engine (shareholding, pledge, auditor events, guidance)."""
    __tablename__ = "facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # shareholding|pledge|auditor_event|guidance|rpt|float
    period: Mapped[Optional[str]] = mapped_column(String(16))
    as_of: Mapped[Optional[date]] = mapped_column(Date)
    payload: Mapped[dict] = mapped_column(JSON)
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    page: Mapped[Optional[int]] = mapped_column(Integer)


engine = create_engine(settings.database_url, future=True)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    (settings.data_dir / "processed").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
