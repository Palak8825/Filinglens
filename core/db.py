"""SQLAlchemy models. One table for documents, one for chunks (with pgvector), one for structured facts."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from core.config import settings


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    doc_type: Mapped[str] = mapped_column(String(32), index=True)  # results|concall|annual_report|shareholding|announcement
    fiscal_period: Mapped[Optional[str]] = mapped_column(String(16))  # e.g. Q2FY26, FY25
    filed_on: Mapped[Optional[date]] = mapped_column(Date)
    title: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)  # local PDF path
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    n_pages: Mapped[int] = mapped_column(Integer, default=0)
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    section: Mapped[Optional[str]] = mapped_column(String(64))  # MD&A, Q&A, notes, speaker name...
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embed_dim))
    document: Mapped[Document] = relationship(back_populates="chunks")


Index("ix_chunks_embedding_hnsw", Chunk.embedding, postgresql_using="hnsw",
      postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"embedding": "vector_cosine_ops"})


class Fact(Base):
    """Structured facts for the red-flag engine (shareholding, pledge, auditor events, guidance)."""
    __tablename__ = "facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # shareholding|pledge|auditor_event|guidance|rpt|float
    period: Mapped[Optional[str]] = mapped_column(String(16))
    as_of: Mapped[Optional[date]] = mapped_column(Date)
    payload: Mapped[dict] = mapped_column(JSONB)
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    page: Mapped[Optional[int]] = mapped_column(Integer)


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
