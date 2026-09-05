"""SQLAlchemy declarative base for persistence models (Phase 3).

No domain ORM tables exist yet (Phase 3A1) -- this module exists so
Alembic's `env.py` has a real `Base.metadata` to diff against starting
with the very first (empty) baseline revision, and so every later phase
imports the base from exactly one place. Persistence models are
deliberately NOT the same classes as `domain/`'s frozen dataclasses --
see docs/ARCHITECTURE.md for why.
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
