"""`TeacherService` (Real-School Setup MVP Slice B): the narrow Teacher
write use case -- create, update, and delete a `Teacher`.

Depends only on the three application-owned repository ports
(`application.ports`) plus the pure `teacher_rules` module -- never
SQLAlchemy, persistence concrete adapters, ORM models, or FastAPI, even
transitively, matching `TeachingAssignmentService`'s own discipline.

Each public method: (1) normalizes and validates the input fields
against the just-loaded snapshot -- a fast, user-friendly failure path
that is explicitly NOT the concurrency guarantee; (2) calls the
`TeacherRepository` write port, passing the *same* pure validation
callable as `validate` -- the port re-invokes it, authoritatively,
against a freshly-reloaded snapshot taken *after* acquiring its
Owner-Decision-#36 row lock, so the two checks can never silently
diverge and a write is never committed against a stale snapshot.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from school_timetable.application import teacher_rules as rules
from school_timetable.application.errors import ConfigurationLockedError
from school_timetable.application.ports import (
    ScheduleVersionRepository,
    SchedulingProblemRepository,
    TeacherRepository,
)
from school_timetable.application.teacher_models import TeacherFields, TeacherWriteResult
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem


def _default_id_factory() -> str:
    """Backend-generated, opaque, stable natural ID -- never a
    persistence surrogate PK, never client-supplied, never a
    name-derived/slug ID."""
    return f"teacher_{uuid4().hex}"


class TeacherService:
    """The narrow Real-School Setup MVP Slice B write use case -- shaped
    like `TeachingAssignmentService`, not a generic repository."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        teacher_repository: TeacherRepository,
        schedule_repository: ScheduleVersionRepository,
        id_factory: Callable[[], str] = _default_id_factory,
    ) -> None:
        self._problem_repository = problem_repository
        self._teacher_repository = teacher_repository
        self._schedule_repository = schedule_repository
        self._id_factory = id_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        fields: TeacherFields,
    ) -> TeacherWriteResult:
        first_name, last_name = rules.normalize_fields(fields.first_name, fields.last_name)
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        natural_id = self._id_factory()

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_create(
                current_problem, school_natural_id, academic_year_natural_id,
                first_name=first_name, last_name=last_name,
            )

        # Fast precheck against the just-loaded snapshot -- the
        # authoritative recheck (identical pure logic) happens again
        # inside the port, under its lock, against a fresh reload.
        validate(problem)

        self._teacher_repository.create(
            school_natural_id, academic_year_natural_id, natural_id,
            first_name, last_name, validate=validate,
        )
        return TeacherWriteResult(
            id=natural_id, first_name=first_name, last_name=last_name,
            name=Teacher(id=natural_id, first_name=first_name, last_name=last_name).full_name,
        )

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        teacher_id: str,
        fields: TeacherFields,
    ) -> TeacherWriteResult:
        first_name, last_name = rules.normalize_fields(fields.first_name, fields.last_name)
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_update(
                current_problem, school_natural_id, academic_year_natural_id, teacher_id,
                first_name=first_name, last_name=last_name,
            )

        validate(problem)

        self._teacher_repository.update(
            school_natural_id, academic_year_natural_id, teacher_id,
            first_name, last_name, validate=validate,
        )
        return TeacherWriteResult(
            id=teacher_id, first_name=first_name, last_name=last_name,
            name=Teacher(id=teacher_id, first_name=first_name, last_name=last_name).full_name,
        )

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        teacher_id: str,
    ) -> None:
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_delete(current_problem, school_natural_id, academic_year_natural_id, teacher_id)

        validate(problem)

        self._teacher_repository.delete(
            school_natural_id, academic_year_natural_id, teacher_id, validate=validate,
        )

    def _precheck_not_locked(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        # Fast, un-locked precheck only -- avoids unnecessary work for
        # the common case, but is explicitly NOT the concurrency
        # guarantee (Owner Decision #36): the authoritative recheck
        # happens inside the write port, under its `AcademicYear` lock.
        existing = self._schedule_repository.get_active_schedule(school_natural_id, academic_year_natural_id)
        if existing is not None:
            raise ConfigurationLockedError(school_natural_id, academic_year_natural_id)
