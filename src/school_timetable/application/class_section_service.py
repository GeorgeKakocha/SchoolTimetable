"""`ClassSectionService` (Real-School Setup MVP Slice C): the narrow
Class write use case -- create, update, and delete a `ClassSection`
together with its owned canonical `WHOLE_CLASS` `ParticipantGroup`
(Owner Decision #33).

Depends only on the three application-owned repository ports
(`application.ports`) plus the pure `class_section_rules` module --
never SQLAlchemy, persistence concrete adapters, ORM models, or
FastAPI, even transitively, matching `TeacherService`'s own discipline.

Each public method: (1) normalizes and validates the input fields
against the just-loaded snapshot -- a fast, user-friendly failure path
that is explicitly NOT the concurrency guarantee; (2) calls the
`ClassSectionRepository` write port, passing the *same* pure validation
callable as `validate` -- the port re-invokes it, authoritatively,
against a freshly-reloaded snapshot taken *after* acquiring its
Owner-Decision-#36 row lock, so the two checks can never silently
diverge and a write is never committed against a stale snapshot.

The administrator never supplies or manages the canonical
`WHOLE_CLASS` group -- both its natural ID and its display name (kept
in sync with the `ClassSection`'s own name) are entirely owned by this
service and its repository.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from school_timetable.application import class_section_rules as rules
from school_timetable.application.class_section_models import ClassSectionFields, ClassSectionWriteResult
from school_timetable.application.errors import ConfigurationLockedError
from school_timetable.application.ports import (
    ClassSectionRepository,
    ScheduleVersionRepository,
    SchedulingProblemRepository,
)
from school_timetable.domain.problem import SchedulingProblem


def _default_class_id_factory() -> str:
    """Backend-generated, opaque, stable natural ID -- never a
    persistence surrogate PK, never client-supplied, never
    name-derived/slug."""
    return f"class_{uuid4().hex}"


def _default_group_id_factory() -> str:
    """Backend-generated, opaque, stable natural ID for the internal
    canonical `WHOLE_CLASS` group -- same discipline as
    `_default_class_id_factory`, kept as an explicit, separate factory
    (rather than one ambiguous factory whose call order becomes part of
    the test contract)."""
    return f"group_{uuid4().hex}"


class ClassSectionService:
    """The narrow Real-School Setup MVP Slice C write use case -- shaped
    like `TeacherService`, not a generic repository."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        class_repository: ClassSectionRepository,
        schedule_repository: ScheduleVersionRepository,
        class_id_factory: Callable[[], str] = _default_class_id_factory,
        group_id_factory: Callable[[], str] = _default_group_id_factory,
    ) -> None:
        self._problem_repository = problem_repository
        self._class_repository = class_repository
        self._schedule_repository = schedule_repository
        self._class_id_factory = class_id_factory
        self._group_id_factory = group_id_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        fields: ClassSectionFields,
    ) -> ClassSectionWriteResult:
        name = rules.normalize_name(fields.name)
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        class_natural_id = self._class_id_factory()
        group_natural_id = self._group_id_factory()

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_create(current_problem, school_natural_id, academic_year_natural_id, name=name)

        # Fast precheck against the just-loaded snapshot -- the
        # authoritative recheck (identical pure logic) happens again
        # inside the port, under its lock, against a fresh reload.
        validate(problem)

        self._class_repository.create(
            school_natural_id, academic_year_natural_id, class_natural_id, group_natural_id,
            name, validate=validate,
        )
        return ClassSectionWriteResult(id=class_natural_id, name=name)

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        class_id: str,
        fields: ClassSectionFields,
    ) -> ClassSectionWriteResult:
        name = rules.normalize_name(fields.name)
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_update(
                current_problem, school_natural_id, academic_year_natural_id, class_id, name=name,
            )

        validate(problem)

        self._class_repository.update(
            school_natural_id, academic_year_natural_id, class_id, name, validate=validate,
        )
        return ClassSectionWriteResult(id=class_id, name=name)

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        class_id: str,
    ) -> None:
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_delete(current_problem, school_natural_id, academic_year_natural_id, class_id)

        validate(problem)

        self._class_repository.delete(
            school_natural_id, academic_year_natural_id, class_id, validate=validate,
        )

    def _precheck_not_locked(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        # Fast, un-locked precheck only -- avoids unnecessary work for
        # the common case, but is explicitly NOT the concurrency
        # guarantee (Owner Decision #36): the authoritative recheck
        # happens inside the write port, under its `AcademicYear` lock.
        existing = self._schedule_repository.get_active_schedule(school_natural_id, academic_year_natural_id)
        if existing is not None:
            raise ConfigurationLockedError(school_natural_id, academic_year_natural_id)
