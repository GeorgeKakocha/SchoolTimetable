"""FastAPI dependency wiring (Phase 3A2.4, extended Phase 3A3.4, Phase
3C.2b): the API composition root.

This is the one place that imports both `application/` (the repository
ports/service) and `persistence/` (the concrete SQLAlchemy adapters)
together -- see `docs/ARCHITECTURE.md`'s locked ports-and-adapters
direction. Routes depend on `application/` Protocols/services, never on
concrete `persistence/` classes directly.

`get_scheduling_problem_repository` (Phase 3A2.4, unchanged) remains
request-scoped -- correct for `/config`'s simple read, backed by the
same `Session` `get_session()` already provides for the whole request.

`get_schedule_version_repository`/`get_generate_schedule_service`
(Phase 3A3.4), `get_teaching_assignment_service`/
`get_teaching_assignments_projection_service` (Phase 3C.2b) are
deliberately NOT built this way: `SqlAlchemyScheduleVersionRepository`/
`SqlAlchemyTeachingAssignmentRepository` have no session-bound
constructor at all, and neither `GenerateScheduleService` nor
`TeachingAssignmentService`'s write port may ever receive a
request-scoped `Session` that would stay open across a CP-SAT solve
(Decision #31 Owner Decision 4) or across the Decision #36
lock/reload/validate sequence. All are instead constructed directly
against `persistence.db.SessionLocal` -- the existing module-level
session *factory*, unchanged -- so every repository call these
dependencies use opens and closes its own short session internally,
exactly as `SqlAlchemyScheduleVersionRepository`/
`SessionFactorySchedulingProblemRepository`/
`SqlAlchemyTeachingAssignmentRepository` already require."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from school_timetable.application.class_timetable_service import ClassTimetableService
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.application.teaching_assignment_service import TeachingAssignmentService
from school_timetable.application.teaching_assignments_projection_service import (
    TeachingAssignmentsProjectionService,
)
from school_timetable.persistence.db import SessionLocal, get_session
from school_timetable.persistence.problem_repository import (
    SessionFactorySchedulingProblemRepository,
    SqlAlchemySchedulingProblemRepository,
)
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.teaching_assignment_repository import (
    SqlAlchemyTeachingAssignmentRepository,
)


def get_scheduling_problem_repository(
    session: Session = Depends(get_session),
) -> SchedulingProblemRepository:
    """One repository per request, backed by the same request-scoped
    `Session` that `get_session()` already provides (one per request,
    always closed afterward) -- no global long-lived `Session`, no
    separate connection constructed here."""
    return SqlAlchemySchedulingProblemRepository(session)


def get_schedule_version_repository() -> ScheduleVersionRepository:
    """Session-factory-backed, never request-scoped: every call
    (`get_active_schedule`) opens and closes its own short `Session`
    against `SessionLocal`."""
    return SqlAlchemyScheduleVersionRepository(SessionLocal)


def get_generate_schedule_service() -> GenerateScheduleService:
    """Composes the two session-factory-backed adapters
    `GenerateScheduleService` needs -- never a request-scoped `Session`
    -- so preflight/solve/verify run with no DB connection held open."""
    return GenerateScheduleService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_class_timetable_service() -> ClassTimetableService:
    """Composes the same two session-factory-backed adapters
    `ClassTimetableService` needs -- never a request-scoped `Session`,
    matching `get_generate_schedule_service` exactly (Phase 3B.1,
    `docs/DECISIONS.md` #32)."""
    return ClassTimetableService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_teaching_assignments_projection_service() -> TeachingAssignmentsProjectionService:
    """Composes the same two session-factory-backed adapters
    `ClassTimetableService` uses -- never a request-scoped `Session`
    (Phase 3C.2b)."""
    return TeachingAssignmentsProjectionService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_teaching_assignment_service() -> TeachingAssignmentService:
    """Composes `TeachingAssignmentService`'s three session-factory-backed
    dependencies -- never a request-scoped `Session`, so the
    `SqlAlchemyTeachingAssignmentRepository`'s own short
    lock/reload/validate transactions (Decision #36) stay entirely its
    own (Phase 3C.2b)."""
    return TeachingAssignmentService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyTeachingAssignmentRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )
