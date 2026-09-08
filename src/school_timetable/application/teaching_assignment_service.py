"""`TeachingAssignmentService` (Phase 3C.2, `docs/DECISIONS.md` #34,
#36): the narrow Teaching Assignment write use case -- create, update,
and delete a plain, ordinary `WHOLE_CLASS` `TeachingRequirement`.

Depends only on the three application-owned repository ports
(`application.ports`) plus the pure `teaching_assignment_rules` module
-- never SQLAlchemy, persistence concrete adapters, ORM models, or
FastAPI, even transitively, matching `GenerateScheduleService`'s own
discipline.

Each public method: (1) performs a fast, un-locked precheck (mirroring
`GenerateScheduleService`'s own early `ScheduleAlreadyExistsError`
precheck) that is explicitly NOT the concurrency guarantee; (2) loads
the current configuration and runs the shared, pure validation rules
against that snapshot for a fast, user-friendly failure path; (3) calls
the `TeachingAssignmentRepository` write port, passing the *same* pure
validation callable as `validate` -- the port re-invokes it,
authoritatively, against a freshly-reloaded snapshot taken *after*
acquiring its Owner-Decision-#36 row lock, so the two checks can never
silently diverge and a write is never committed against a stale
snapshot.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from school_timetable.application import teaching_assignment_rules as rules
from school_timetable.application.errors import ConfigurationLockedError
from school_timetable.application.ports import (
    ScheduleVersionRepository,
    SchedulingProblemRepository,
    TeachingAssignmentRepository,
)
from school_timetable.application.teaching_assignment_models import (
    TeachingAssignmentFields,
    TeachingAssignmentWriteResult,
)
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.validation.errors import ValidationError


def _default_id_factory() -> str:
    """Backend-generated, opaque, stable natural ID (Decision #34) --
    never a persistence surrogate PK, never client-supplied, never a
    sequential/human-readable counter (which would need its own
    concurrency handling for no benefit the admin actually sees)."""
    return f"req_{uuid4().hex}"


class TeachingAssignmentService:
    """The narrow Phase 3C.2 configuration write use case (Decision #34)
    -- shaped like `GenerateScheduleService`, not a generic repository."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        teaching_assignment_repository: TeachingAssignmentRepository,
        schedule_repository: ScheduleVersionRepository,
        id_factory: Callable[[], str] = _default_id_factory,
    ) -> None:
        self._problem_repository = problem_repository
        self._teaching_assignment_repository = teaching_assignment_repository
        self._schedule_repository = schedule_repository
        self._id_factory = id_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        fields: TeachingAssignmentFields,
    ) -> TeachingAssignmentWriteResult:
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        natural_id = self._id_factory()
        warnings_holder: list[tuple[ValidationError, ...]] = [()]

        def validate(current_problem: SchedulingProblem) -> None:
            warnings_holder[0] = rules.validate_create(
                current_problem, school_natural_id, academic_year_natural_id,
                teacher_id=fields.teacher_id, participant_group_id=fields.participant_group_id,
                activity_id=fields.activity_id, weekly_periods=fields.weekly_periods,
            )

        # Fast precheck against the just-loaded snapshot -- the
        # authoritative recheck (identical pure logic) happens again
        # inside the port, under its lock, against a fresh reload.
        validate(problem)

        self._teaching_assignment_repository.create(
            school_natural_id, academic_year_natural_id, natural_id,
            fields.teacher_id, fields.participant_group_id, fields.activity_id, fields.weekly_periods,
            validate=validate,
        )
        return TeachingAssignmentWriteResult(natural_id=natural_id, warnings=warnings_holder[0])

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        natural_id: str,
        fields: TeachingAssignmentFields,
    ) -> TeachingAssignmentWriteResult:
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        warnings_holder: list[tuple[ValidationError, ...]] = [()]

        def validate(current_problem: SchedulingProblem) -> None:
            warnings_holder[0] = rules.validate_update(
                current_problem, school_natural_id, academic_year_natural_id, natural_id,
                teacher_id=fields.teacher_id, participant_group_id=fields.participant_group_id,
                activity_id=fields.activity_id, weekly_periods=fields.weekly_periods,
            )

        validate(problem)

        self._teaching_assignment_repository.update(
            school_natural_id, academic_year_natural_id, natural_id,
            fields.teacher_id, fields.participant_group_id, fields.activity_id, fields.weekly_periods,
            validate=validate,
        )
        return TeachingAssignmentWriteResult(natural_id=natural_id, warnings=warnings_holder[0])

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        natural_id: str,
    ) -> tuple[ValidationError, ...]:
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        warnings_holder: list[tuple[ValidationError, ...]] = [()]

        def validate(current_problem: SchedulingProblem) -> None:
            warnings_holder[0] = rules.validate_delete(
                current_problem, school_natural_id, academic_year_natural_id, natural_id,
            )

        validate(problem)

        self._teaching_assignment_repository.delete(
            school_natural_id, academic_year_natural_id, natural_id, validate=validate,
        )
        return warnings_holder[0]

    def _precheck_not_locked(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        # Fast, un-locked precheck only -- avoids unnecessary work for
        # the common case, but is explicitly NOT the concurrency
        # guarantee (Owner Decision #36): the authoritative recheck
        # happens inside the write port, under its `AcademicYear` lock.
        existing = self._schedule_repository.get_active_schedule(school_natural_id, academic_year_natural_id)
        if existing is not None:
            raise ConfigurationLockedError(school_natural_id, academic_year_natural_id)
