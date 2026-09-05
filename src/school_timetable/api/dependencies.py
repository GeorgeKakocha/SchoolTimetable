"""FastAPI dependency wiring (Phase 3A2.4): the API composition root.

This is the one place that imports both `application/` (the repository
port) and `persistence/` (the concrete SQLAlchemy adapter) together --
see `docs/ARCHITECTURE.md`'s locked ports-and-adapters direction. Routes
depend on `SchedulingProblemRepository` (the `application/` Protocol),
never on `SqlAlchemySchedulingProblemRepository` directly.
"""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from school_timetable.application.ports import SchedulingProblemRepository
from school_timetable.persistence.db import get_session
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository


def get_scheduling_problem_repository(
    session: Session = Depends(get_session),
) -> SchedulingProblemRepository:
    """One repository per request, backed by the same request-scoped
    `Session` that `get_session()` already provides (one per request,
    always closed afterward) -- no global long-lived `Session`, no
    separate connection constructed here."""
    return SqlAlchemySchedulingProblemRepository(session)
