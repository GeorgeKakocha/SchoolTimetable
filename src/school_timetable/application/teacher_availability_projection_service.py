"""`TeacherAvailabilityProjectionService` (Owner Decision #38): the
read-only Teacher Availability page projection -- mirrors
`TeacherProjectionService`'s projection pattern exactly, kept entirely
separate from `TeacherAvailabilityService` (the write use case) the
same way every existing projection service is kept separate from its
write-service sibling.

Depends only on the two existing application-owned repository ports
(`application.ports`) -- never SQLAlchemy, persistence concrete
adapters, ORM models, or FastAPI, even transitively. No DB `Session`/
connection is held by this service itself.

This projection is deliberately a *sparse exception* view: it never
projects an `AVAILABLE` row (whether that arises from the sparse
default -- no row at all -- or, in principle, a legacy explicit
`AVAILABLE` row somehow persisted) -- only `PREFER_NOT`/`UNAVAILABLE`
exceptions are ever included. `GET /config`'s own
`teacher_availabilities` projection is untouched by this module and
keeps exposing every persisted row (including any legacy `AVAILABLE`
one) under its own, unrelated, general-configuration contract.
"""
from __future__ import annotations

from school_timetable.application.ports import ConfigurationRevisionRepository, SchedulingProblemRepository
from school_timetable.application.teacher_availability_projection_models import (
    TeacherAvailabilityDayItem,
    TeacherAvailabilityExceptionItem,
    TeacherAvailabilityPeriodItem,
    TeacherAvailabilityProjectionView,
    TeacherAvailabilityTeacherItem,
)
from school_timetable.domain.people import AvailabilityStatus

_EXCEPTION_STATUSES = frozenset({AvailabilityStatus.PREFER_NOT.value, AvailabilityStatus.UNAVAILABLE.value})


class TeacherAvailabilityProjectionService:
    """Depends only on the two existing repository ports -- never
    SQLAlchemy, persistence concrete adapters, ORM models, or FastAPI."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        configuration_revision_repository: ConfigurationRevisionRepository,
    ) -> None:
        self._problem_repository = problem_repository
        self._configuration_revision_repository = configuration_revision_repository

    def project(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> TeacherAvailabilityProjectionView:
        # (1) Load config. SchedulingProblemNotFoundError propagates
        # unchanged if the school/year itself does not resolve.
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        # (2) Safe Configuration Changes, Slice B: `configuration_locked`
        # is `True` exactly when no draft is currently open -- reads
        # remain available regardless of lock state; only writes reject.
        configuration_locked = self._configuration_revision_repository.get_state(
            school_natural_id, academic_year_natural_id,
        ).configuration_locked

        # (3) Build the view purely in memory -- no further DB access.
        # `problem.teachers`/`problem.days`/`problem.periods` all
        # already carry their own authoritative ordinal/index order
        # (`problem_repository.py`) -- never re-sorted here.
        teachers = tuple(
            TeacherAvailabilityTeacherItem(id=t.id, name=t.full_name) for t in problem.teachers
        )
        days = tuple(
            TeacherAvailabilityDayItem(id=d.id, name=d.name, index=d.index) for d in problem.days
        )
        periods = tuple(
            TeacherAvailabilityPeriodItem(
                id=p.id, name=p.name, index=p.index, block_id=p.block_id, is_instructional=p.is_instructional,
            )
            for p in problem.periods
        )
        exceptions = tuple(
            TeacherAvailabilityExceptionItem(
                teacher_id=a.teacher_id, day_id=a.day_id, period_id=a.period_id, status=a.status.value,
            )
            for a in problem.teacher_availabilities
            if a.status.value in _EXCEPTION_STATUSES
        )

        return TeacherAvailabilityProjectionView(
            configuration_locked=configuration_locked,
            teachers=teachers,
            days=days,
            periods=periods,
            exceptions=exceptions,
        )
