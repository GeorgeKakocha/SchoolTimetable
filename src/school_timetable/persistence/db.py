"""SQLAlchemy engine/session infrastructure (Phase 3).

This is the only module that constructs a process-wide `Engine`. Nothing
in `domain/`, `scheduling/`, `validation/`, or `verification/` may import
this module (or `sqlalchemy` at all) -- see docs/ARCHITECTURE.md. It is
also never imported by `application/` in later phases, which depends only
on repository ports/interfaces; only `persistence/` adapters and the
`api/` composition root import this module directly.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.config import get_settings


def create_db_engine(database_url: str | None = None) -> Engine:
    """Build a new `Engine` for `database_url` (or the configured
    `DATABASE_URL` if omitted). A separate function, not just the
    module-level `engine` below, so tests and Alembic can each construct
    an engine bound to a different URL (e.g. a test database) without
    reloading this module."""
    url = database_url or get_settings().database_url
    return create_engine(url, pool_pre_ping=True)


engine = create_db_engine()
"""The application's default engine, bound to `DATABASE_URL` at import
time. `create_engine` does not open a connection eagerly, so importing
this module is always safe even if no database is reachable yet."""

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency: one `Session` per request, always closed
    afterward regardless of success or failure."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
