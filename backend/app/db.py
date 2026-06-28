"""SQLAlchemy engine + session factory.

Single source for DB connectivity so the audit layer and (later) the
pgvector memory layer share one engine.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_engine(_settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session and always closes it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
