"""`ReservedActivityService` (Reserved Activities Slice A2): the narrow
Reserved Activity write use case -- create, update, and delete a
`ReservedBlock`.

"Reserved Activity" is not a new domain entity -- it is the
user-facing name for `ReservedBlock` (see `domain/blocks.py`).

Depends only on the two application-owned repository ports
(`application.ports`) plus the pure `reserved_activity_rules` module --
never SQLAlchemy, persistence concrete adapters, ORM models, or
FastAPI, even transitively, matching `SpecialActivityService`'s own
discipline.

Each public method: (1) normalizes the input into the tuple/plain
shapes `reserved_activity_rules` expects and validates it against the
just-loaded snapshot -- a fast, user-friendly failure path that is
explicitly NOT the concurrency guarantee; (2) calls the
`ReservedActivityRepository` write port, passing the *same* pure
validation callable as `validate` -- the port re-invokes it,
authoritatively, against a freshly-reloaded snapshot taken *after*
acquiring its Owner-Decision-#36 row lock, so the two checks can never
silently diverge and a write is never committed against a stale
snapshot.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from school_timetable.application import reserved_activity_rules as rules
from school_timetable.application.ports import (
    ReservedActivityRepository,
    SchedulingProblemRepository,
)
from school_timetable.application.reserved_activity_models import ReservedActivityFields, ReservedActivityWriteResult
from school_timetable.domain.problem import SchedulingProblem


def _default_reserved_activity_id_factory() -> str:
    """Backend-generated, opaque, stable natural ID -- never a
    persistence surrogate PK, never client-supplied, never
    name-derived/slug. Uses the `reserved_block_` prefix (not
    `reserved_activity_`) because the persisted/domain entity genuinely
    is a `ReservedBlock`; "Reserved Activity" is a presentation-layer
    label over it, never a distinct stored identity -- mirrors
    `special_activity_service._default_activity_id_factory` exactly."""
    return f"reserved_block_{uuid4().hex}"


class ReservedActivityService:
    """The narrow Reserved Activities Slice A2 write use case -- shaped
    like `SpecialActivityService`, not a generic repository."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        reserved_activity_repository: ReservedActivityRepository,
        reserved_activity_id_factory: Callable[[], str] = _default_reserved_activity_id_factory,
    ) -> None:
        self._problem_repository = problem_repository
        self._reserved_activity_repository = reserved_activity_repository
        self._reserved_activity_id_factory = reserved_activity_id_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        fields: ReservedActivityFields,
    ) -> ReservedActivityWriteResult:
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        reserved_activity_id = self._reserved_activity_id_factory()
        slot_pairs = tuple((s.day_id, s.period_id) for s in fields.slots)

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_create(
                current_problem, school_natural_id, academic_year_natural_id, reserved_activity_id,
                special_activity_id=fields.special_activity_id, class_section_ids=fields.class_section_ids,
                teacher_id=fields.teacher_id, slots=slot_pairs, resource_id=fields.resource_id,
            )

        # Fast precheck against the just-loaded snapshot -- the
        # authoritative recheck (identical pure logic) happens again
        # inside the port, under its lock, against a fresh reload.
        validate(problem)

        return self._reserved_activity_repository.create(
            school_natural_id, academic_year_natural_id, reserved_activity_id, fields, validate=validate,
        )

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        reserved_activity_id: str,
        fields: ReservedActivityFields,
    ) -> ReservedActivityWriteResult:
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        slot_pairs = tuple((s.day_id, s.period_id) for s in fields.slots)

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_update(
                current_problem, school_natural_id, academic_year_natural_id, reserved_activity_id,
                special_activity_id=fields.special_activity_id, class_section_ids=fields.class_section_ids,
                teacher_id=fields.teacher_id, slots=slot_pairs, resource_id=fields.resource_id,
            )

        validate(problem)

        return self._reserved_activity_repository.update(
            school_natural_id, academic_year_natural_id, reserved_activity_id, fields, validate=validate,
        )

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        reserved_activity_id: str,
    ) -> None:
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_delete(current_problem, school_natural_id, academic_year_natural_id, reserved_activity_id)

        validate(problem)

        self._reserved_activity_repository.delete(
            school_natural_id, academic_year_natural_id, reserved_activity_id, validate=validate,
        )
