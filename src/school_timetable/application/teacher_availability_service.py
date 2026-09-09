"""`TeacherAvailabilityService` (Owner Decision #38): the narrow
Teacher Availability write use case -- replacing one Teacher's
complete sparse `PREFER_NOT`/`UNAVAILABLE` exception set.

Depends only on the three application-owned repository ports
(`application.ports`) plus the pure `teacher_availability_rules`
module -- never SQLAlchemy, persistence concrete adapters, ORM models,
or FastAPI, even transitively, matching `TeacherService`'s own
discipline.

`replace_exceptions`: (1) validates the request's *shape* against the
just-loaded snapshot -- a fast, user-friendly failure path that is
explicitly NOT the concurrency guarantee; (2) calls the
`TeacherAvailabilityRepository` write port, passing the *same* pure
validation callable as `validate` -- the port re-invokes it,
authoritatively, against a freshly-reloaded snapshot taken *after*
acquiring its Owner-Decision-#36 row lock, so the two checks can never
silently diverge and a write is never committed against a stale
snapshot.
"""
from __future__ import annotations

from school_timetable.application import teacher_availability_rules as rules
from school_timetable.application.errors import ConfigurationLockedError
from school_timetable.application.ports import (
    ScheduleVersionRepository,
    SchedulingProblemRepository,
    TeacherAvailabilityRepository,
)
from school_timetable.application.teacher_availability_models import (
    TeacherAvailabilityExceptionResult,
    TeacherAvailabilityReplaceFields,
    TeacherAvailabilityWriteResult,
)
from school_timetable.domain.problem import SchedulingProblem


class TeacherAvailabilityService:
    """The narrow Owner Decision #38 write use case -- shaped like
    `TeacherService`, not a generic repository."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        teacher_availability_repository: TeacherAvailabilityRepository,
        schedule_repository: ScheduleVersionRepository,
    ) -> None:
        self._problem_repository = problem_repository
        self._teacher_availability_repository = teacher_availability_repository
        self._schedule_repository = schedule_repository

    def replace_exceptions(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        teacher_id: str,
        fields: TeacherAvailabilityReplaceFields,
    ) -> TeacherAvailabilityWriteResult:
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        exceptions = fields.exceptions

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_replace(
                current_problem, school_natural_id, academic_year_natural_id, teacher_id,
                exceptions=exceptions,
            )

        # Fast precheck against the just-loaded snapshot -- the
        # authoritative recheck (identical pure logic) happens again
        # inside the port, under its lock, against a fresh reload.
        validate(problem)

        self._teacher_availability_repository.replace_exceptions(
            school_natural_id, academic_year_natural_id, teacher_id, exceptions, validate=validate,
        )
        return TeacherAvailabilityWriteResult(
            teacher_id=teacher_id,
            exceptions=tuple(
                TeacherAvailabilityExceptionResult(day_id=e.day_id, period_id=e.period_id, status=e.status)
                for e in exceptions
            ),
        )

    def _precheck_not_locked(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        # Fast, un-locked precheck only -- avoids unnecessary work for
        # the common case, but is explicitly NOT the concurrency
        # guarantee (Owner Decision #36): the authoritative recheck
        # happens inside the write port, under its `AcademicYear` lock.
        existing = self._schedule_repository.get_active_schedule(school_natural_id, academic_year_natural_id)
        if existing is not None:
            raise ConfigurationLockedError(school_natural_id, academic_year_natural_id)
