"""`SubjectService` (Real-School Setup MVP Slice D): the narrow Subject
write use case -- create, update, and delete an `Activity(kind=ORDINARY)`.

"Subject" is not a new domain entity -- it is the user-facing name for
`Activity(kind=ORDINARY)` (see `domain/activities.py`). This service
never mutates `Activity.kind`; create always persists `ORDINARY`, and
update/delete only ever resolve an existing `ORDINARY` target -- a
`CLUB` activity ID is treated as not found (`SubjectNotFoundError`),
never as a wrong-kind conflict, matching the filtered-resource-surface
contract locked in the Slice D design gate.

Depends only on the two application-owned repository ports
(`application.ports`) plus the pure `subject_rules` module -- never
SQLAlchemy, persistence concrete adapters, ORM models, or FastAPI, even
transitively, matching `ClassSectionService`'s own discipline.

Each public method: (1) normalizes and validates the input fields
against the just-loaded snapshot -- a fast, user-friendly failure path
that is explicitly NOT the concurrency guarantee; (2) calls the
`ActivityRepository` write port, passing the *same* pure validation
callable as `validate` -- the port re-invokes it, authoritatively,
against a freshly-reloaded snapshot taken *after* acquiring its
Owner-Decision-#36 row lock, so the two checks can never silently
diverge and a write is never committed against a stale snapshot.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from school_timetable.application import subject_rules as rules
from school_timetable.application.ports import (
    ActivityRepository,
    SchedulingProblemRepository,
)
from school_timetable.application.subject_models import SubjectFields, SubjectWriteResult
from school_timetable.domain.problem import SchedulingProblem


def _default_activity_id_factory() -> str:
    """Backend-generated, opaque, stable natural ID -- never a
    persistence surrogate PK, never client-supplied, never
    name-derived/slug. Uses the `activity_` prefix (not `subject_`)
    because the persisted/domain entity genuinely is an `Activity`;
    "Subject" is a presentation-layer label over it, never a distinct
    stored identity."""
    return f"activity_{uuid4().hex}"


class SubjectService:
    """The narrow Real-School Setup MVP Slice D write use case -- shaped
    like `ClassSectionService`, not a generic repository."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        activity_repository: ActivityRepository,
        activity_id_factory: Callable[[], str] = _default_activity_id_factory,
    ) -> None:
        self._problem_repository = problem_repository
        self._activity_repository = activity_repository
        self._activity_id_factory = activity_id_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        fields: SubjectFields,
    ) -> SubjectWriteResult:
        name = rules.normalize_name(fields.name)
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        subject_natural_id = self._activity_id_factory()

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_create(current_problem, school_natural_id, academic_year_natural_id, name=name)

        # Fast precheck against the just-loaded snapshot -- the
        # authoritative recheck (identical pure logic) happens again
        # inside the port, under its lock, against a fresh reload.
        validate(problem)

        self._activity_repository.create(
            school_natural_id, academic_year_natural_id, subject_natural_id, name, validate=validate,
        )
        return SubjectWriteResult(id=subject_natural_id, name=name)

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        subject_id: str,
        fields: SubjectFields,
    ) -> SubjectWriteResult:
        name = rules.normalize_name(fields.name)
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_update(
                current_problem, school_natural_id, academic_year_natural_id, subject_id, name=name,
            )

        validate(problem)

        self._activity_repository.update(
            school_natural_id, academic_year_natural_id, subject_id, name, validate=validate,
        )
        return SubjectWriteResult(id=subject_id, name=name)

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        subject_id: str,
    ) -> None:
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_delete(current_problem, school_natural_id, academic_year_natural_id, subject_id)

        validate(problem)

        self._activity_repository.delete(
            school_natural_id, academic_year_natural_id, subject_id, validate=validate,
        )
