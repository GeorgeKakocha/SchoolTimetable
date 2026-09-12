"""`SpecialActivityService` (Reserved Activities Slice A1): the narrow
Special Activity write use case -- create, update, and delete an
`Activity(kind=CLUB)`.

"Special Activity" is not a new domain entity -- it is the user-facing
name for `Activity(kind=CLUB)` (see `domain/activities.py`). This
service never mutates `Activity.kind`; create always persists `CLUB`,
and update/delete only ever resolve an existing `CLUB` target -- an
`ORDINARY` activity ID is treated as not found
(`SpecialActivityNotFoundError`), never as a wrong-kind conflict,
mirroring `SubjectService`'s own filtered-resource-surface contract.

Depends only on the two application-owned repository ports
(`application.ports`) plus the pure `special_activity_rules` module --
never SQLAlchemy, persistence concrete adapters, ORM models, or
FastAPI, even transitively, matching `SubjectService`'s own
discipline.

Each public method: (1) normalizes and validates the input fields
against the just-loaded snapshot -- a fast, user-friendly failure path
that is explicitly NOT the concurrency guarantee; (2) calls the
`SpecialActivityRepository` write port, passing the *same* pure
validation callable as `validate` -- the port re-invokes it,
authoritatively, against a freshly-reloaded snapshot taken *after*
acquiring its Owner-Decision-#36 row lock, so the two checks can never
silently diverge and a write is never committed against a stale
snapshot. `update`'s dependent `ReservedBlock.name` synchronization is
entirely the repository's own responsibility (within the same
transaction as the Activity rename) -- this service never touches
`ReservedBlock` itself.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from school_timetable.application import special_activity_rules as rules
from school_timetable.application.ports import (
    SchedulingProblemRepository,
    SpecialActivityRepository,
)
from school_timetable.application.special_activity_models import (
    SpecialActivityFields,
    SpecialActivityWriteResult,
)
from school_timetable.domain.problem import SchedulingProblem


def _default_activity_id_factory() -> str:
    """Backend-generated, opaque, stable natural ID -- never a
    persistence surrogate PK, never client-supplied, never
    name-derived/slug. Uses the `activity_` prefix (not
    `special_activity_`) because the persisted/domain entity genuinely
    is an `Activity`; "Special Activity" is a presentation-layer label
    over it, never a distinct stored identity -- mirrors
    `subject_service._default_activity_id_factory` exactly."""
    return f"activity_{uuid4().hex}"


class SpecialActivityService:
    """The narrow Reserved Activities Slice A1 write use case -- shaped
    like `SubjectService`, not a generic repository."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        special_activity_repository: SpecialActivityRepository,
        activity_id_factory: Callable[[], str] = _default_activity_id_factory,
    ) -> None:
        self._problem_repository = problem_repository
        self._special_activity_repository = special_activity_repository
        self._activity_id_factory = activity_id_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        fields: SpecialActivityFields,
    ) -> SpecialActivityWriteResult:
        name = rules.normalize_name(fields.name)
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        special_activity_natural_id = self._activity_id_factory()

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_create(current_problem, school_natural_id, academic_year_natural_id, name=name)

        # Fast precheck against the just-loaded snapshot -- the
        # authoritative recheck (identical pure logic) happens again
        # inside the port, under its lock, against a fresh reload.
        validate(problem)

        self._special_activity_repository.create(
            school_natural_id, academic_year_natural_id, special_activity_natural_id, name, validate=validate,
        )
        return SpecialActivityWriteResult(id=special_activity_natural_id, name=name)

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        special_activity_id: str,
        fields: SpecialActivityFields,
    ) -> SpecialActivityWriteResult:
        name = rules.normalize_name(fields.name)
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_update(
                current_problem, school_natural_id, academic_year_natural_id, special_activity_id, name=name,
            )

        validate(problem)

        self._special_activity_repository.update(
            school_natural_id, academic_year_natural_id, special_activity_id, name, validate=validate,
        )
        return SpecialActivityWriteResult(id=special_activity_id, name=name)

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        special_activity_id: str,
    ) -> None:
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_delete(current_problem, school_natural_id, academic_year_natural_id, special_activity_id)

        validate(problem)

        self._special_activity_repository.delete(
            school_natural_id, academic_year_natural_id, special_activity_id, validate=validate,
        )
