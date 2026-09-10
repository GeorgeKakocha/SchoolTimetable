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

from school_timetable.application.class_section_projection_service import ClassSectionProjectionService
from school_timetable.application.class_section_service import ClassSectionService
from school_timetable.application.class_timetable_service import ClassTimetableService
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.application.reserved_activity_projection_service import ReservedActivityProjectionService
from school_timetable.application.reserved_activity_service import ReservedActivityService
from school_timetable.application.resource_projection_service import ResourceProjectionService
from school_timetable.application.resource_service import ResourceService
from school_timetable.application.special_activity_projection_service import SpecialActivityProjectionService
from school_timetable.application.special_activity_service import SpecialActivityService
from school_timetable.application.subject_projection_service import SubjectProjectionService
from school_timetable.application.subject_service import SubjectService
from school_timetable.application.teacher_availability_projection_service import (
    TeacherAvailabilityProjectionService,
)
from school_timetable.application.teacher_availability_service import TeacherAvailabilityService
from school_timetable.application.teacher_projection_service import TeacherProjectionService
from school_timetable.application.teacher_service import TeacherService
from school_timetable.application.teacher_timetable_service import TeacherTimetableService
from school_timetable.application.teaching_assignment_service import TeachingAssignmentService
from school_timetable.application.teaching_assignments_projection_service import (
    TeachingAssignmentsProjectionService,
)
from school_timetable.persistence.activity_repository import SqlAlchemyActivityRepository
from school_timetable.persistence.class_section_repository import SqlAlchemyClassSectionRepository
from school_timetable.persistence.db import SessionLocal, get_session
from school_timetable.persistence.problem_repository import (
    SessionFactorySchedulingProblemRepository,
    SqlAlchemySchedulingProblemRepository,
)
from school_timetable.persistence.reserved_activity_repository import SqlAlchemyReservedActivityRepository
from school_timetable.persistence.resource_repository import SqlAlchemyResourceRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.special_activity_repository import SqlAlchemySpecialActivityRepository
from school_timetable.persistence.teacher_availability_repository import (
    SqlAlchemyTeacherAvailabilityRepository,
)
from school_timetable.persistence.teacher_repository import SqlAlchemyTeacherRepository
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


def get_teacher_timetable_service() -> TeacherTimetableService:
    """Composes the identical two session-factory-backed adapters
    `ClassTimetableService` uses -- never a request-scoped `Session` --
    for the sibling teacher-timetable projection (next product slice
    after Phase 3C.3, no new phase number)."""
    return TeacherTimetableService(
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


def get_teachers_projection_service() -> TeacherProjectionService:
    """Composes the same two session-factory-backed adapters
    `ClassTimetableService` uses -- never a request-scoped `Session`
    (Real-School Setup MVP Slice B)."""
    return TeacherProjectionService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_teacher_service() -> TeacherService:
    """Composes `TeacherService`'s three session-factory-backed
    dependencies -- never a request-scoped `Session`, so
    `SqlAlchemyTeacherRepository`'s own short lock/reload/validate
    transactions (Decision #36) stay entirely its own (Real-School
    Setup MVP Slice B)."""
    return TeacherService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyTeacherRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_classes_projection_service() -> ClassSectionProjectionService:
    """Composes the same two session-factory-backed adapters
    `ClassTimetableService` uses -- never a request-scoped `Session`
    (Real-School Setup MVP Slice C)."""
    return ClassSectionProjectionService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_class_section_service() -> ClassSectionService:
    """Composes `ClassSectionService`'s three session-factory-backed
    dependencies -- never a request-scoped `Session`, so
    `SqlAlchemyClassSectionRepository`'s own short lock/reload/validate
    transactions (Decision #36) stay entirely its own (Real-School
    Setup MVP Slice C)."""
    return ClassSectionService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyClassSectionRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_subjects_projection_service() -> SubjectProjectionService:
    """Composes the same two session-factory-backed adapters
    `ClassTimetableService` uses -- never a request-scoped `Session`
    (Real-School Setup MVP Slice D)."""
    return SubjectProjectionService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_subject_service() -> SubjectService:
    """Composes `SubjectService`'s three session-factory-backed
    dependencies -- never a request-scoped `Session`, so
    `SqlAlchemyActivityRepository`'s own short lock/reload/validate
    transactions (Decision #36) stay entirely its own (Real-School
    Setup MVP Slice D)."""
    return SubjectService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyActivityRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_special_activities_projection_service() -> SpecialActivityProjectionService:
    """Composes the same two session-factory-backed adapters
    `SubjectProjectionService` uses -- never a request-scoped `Session`
    (Reserved Activities Slice A1)."""
    return SpecialActivityProjectionService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_special_activity_service() -> SpecialActivityService:
    """Composes `SpecialActivityService`'s three session-factory-backed
    dependencies -- never a request-scoped `Session`, so
    `SqlAlchemySpecialActivityRepository`'s own short lock/reload/
    validate transactions (Decision #36) stay entirely its own
    (Reserved Activities Slice A1)."""
    return SpecialActivityService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemySpecialActivityRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_resources_projection_service() -> ResourceProjectionService:
    """Composes the same two session-factory-backed adapters
    `SpecialActivityProjectionService` uses -- never a request-scoped
    `Session` (Resources Slice A)."""
    return ResourceProjectionService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_resource_service() -> ResourceService:
    """Composes `ResourceService`'s three session-factory-backed
    dependencies -- never a request-scoped `Session`, so
    `SqlAlchemyResourceRepository`'s own short lock/reload/validate
    transactions (Decision #36) stay entirely its own (Resources
    Slice A)."""
    return ResourceService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyResourceRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_reserved_activities_projection_service() -> ReservedActivityProjectionService:
    """Composes the same two session-factory-backed adapters
    `SpecialActivityProjectionService` uses -- never a request-scoped
    `Session` (Reserved Activities Slice A2)."""
    return ReservedActivityProjectionService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_reserved_activity_service() -> ReservedActivityService:
    """Composes `ReservedActivityService`'s three session-factory-backed
    dependencies -- never a request-scoped `Session`, so
    `SqlAlchemyReservedActivityRepository`'s own short lock/reload/
    validate transactions (Decision #36) stay entirely its own
    (Reserved Activities Slice A2)."""
    return ReservedActivityService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyReservedActivityRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_teacher_availability_projection_service() -> TeacherAvailabilityProjectionService:
    """Composes the same two session-factory-backed adapters
    `ClassTimetableService` uses -- never a request-scoped `Session`
    (Owner Decision #38)."""
    return TeacherAvailabilityProjectionService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )


def get_teacher_availability_service() -> TeacherAvailabilityService:
    """Composes `TeacherAvailabilityService`'s three session-factory-backed
    dependencies -- never a request-scoped `Session`, so
    `SqlAlchemyTeacherAvailabilityRepository`'s own short
    lock/reload/validate transactions (Decision #36) stay entirely its
    own (Owner Decision #38)."""
    return TeacherAvailabilityService(
        SessionFactorySchedulingProblemRepository(SessionLocal),
        SqlAlchemyTeacherAvailabilityRepository(SessionLocal),
        SqlAlchemyScheduleVersionRepository(SessionLocal),
    )
