"""`CalendarService` (Calendar A): the narrow Day/Period catalog write
use case -- create, update, delete, and reorder (`move`) a
`domain.calendar.Day`/`Period`.

Depends only on the three application-owned repository ports
(`application.ports`) plus the pure `calendar_rules` module -- never
SQLAlchemy, persistence concrete adapters, ORM models, or FastAPI, even
transitively, matching `ResourceService`'s own discipline.

Each public method: (1) normalizes and validates the input fields
against the just-loaded snapshot -- a fast, user-friendly failure path
that is explicitly NOT the concurrency guarantee; (2) calls the
matching repository write port, passing the *same* pure validation
callable as `validate` -- the port re-invokes it, authoritatively,
against a freshly-reloaded snapshot taken *after* acquiring its
Owner-Decision-#36 row lock, so the two checks can never silently
diverge and a write is never committed against a stale snapshot.
"""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from school_timetable.application import calendar_rules as rules
from school_timetable.application.calendar_models import (
    DayFields,
    DayWriteResult,
    PeriodFields,
    PeriodWriteResult,
)
from school_timetable.application.ports import (
    CalendarDayRepository,
    CalendarPeriodRepository,
    SchedulingProblemRepository,
)
from school_timetable.domain.calendar import Period
from school_timetable.domain.problem import SchedulingProblem


def _default_day_id_factory() -> str:
    """Backend-generated, opaque, stable natural ID -- never a
    persistence surrogate PK, never client-supplied, matching every
    other catalog's `f"{prefix}_{uuid4().hex}"` convention exactly.
    Fixture-authored Days (e.g. `"mon"`) predate any write path and are
    untouched by this factory."""
    return f"day_{uuid4().hex}"


def _default_period_id_factory() -> str:
    return f"period_{uuid4().hex}"


class CalendarService:
    """The narrow Calendar A write use case -- shaped like
    `ResourceService`, not a generic repository, with eight methods
    (create/update/delete/move for both Day and Period) rather than
    two separate services, since both write ports share one
    `AcademicYear` scope and one lock/recheck discipline."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        day_repository: CalendarDayRepository,
        period_repository: CalendarPeriodRepository,
        day_id_factory=_default_day_id_factory,
        period_id_factory=_default_period_id_factory,
    ) -> None:
        self._problem_repository = problem_repository
        self._day_repository = day_repository
        self._period_repository = period_repository
        self._day_id_factory = day_id_factory
        self._period_id_factory = period_id_factory

    # -- Day --------------------------------------------------------------

    def day_create(
        self, school_natural_id: str, academic_year_natural_id: str, fields: DayFields,
    ) -> DayWriteResult:
        name = rules.normalize_name(fields.name)
        problem = self._problem_repository.load_by_school_and_year(school_natural_id, academic_year_natural_id)
        day_natural_id = self._day_id_factory()

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_day_create(current_problem, school_natural_id, academic_year_natural_id, name=name)

        validate(problem)

        return self._day_repository.create(
            school_natural_id, academic_year_natural_id, day_natural_id, name, validate=validate,
        )

    def day_update(
        self, school_natural_id: str, academic_year_natural_id: str, day_id: str, fields: DayFields,
    ) -> DayWriteResult:
        name = rules.normalize_name(fields.name)
        problem = self._problem_repository.load_by_school_and_year(school_natural_id, academic_year_natural_id)

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_day_update(
                current_problem, school_natural_id, academic_year_natural_id, day_id, name=name,
            )

        validate(problem)

        return self._day_repository.update(
            school_natural_id, academic_year_natural_id, day_id, name, validate=validate,
        )

    def day_delete(self, school_natural_id: str, academic_year_natural_id: str, day_id: str) -> None:
        problem = self._problem_repository.load_by_school_and_year(school_natural_id, academic_year_natural_id)

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_day_delete(current_problem, school_natural_id, academic_year_natural_id, day_id)

        validate(problem)

        self._day_repository.delete(school_natural_id, academic_year_natural_id, day_id, validate=validate)

    def day_move(
        self, school_natural_id: str, academic_year_natural_id: str, day_id: str, direction: str,
    ) -> None:
        problem = self._problem_repository.load_by_school_and_year(school_natural_id, academic_year_natural_id)

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_day_move(current_problem, school_natural_id, academic_year_natural_id, day_id, direction)

        validate(problem)

        self._day_repository.move(school_natural_id, academic_year_natural_id, day_id, direction, validate=validate)

    # -- Period -------------------------------------------------------------

    def period_create(
        self, school_natural_id: str, academic_year_natural_id: str, fields: PeriodFields,
    ) -> PeriodWriteResult:
        name = rules.normalize_name(fields.name)
        problem = self._problem_repository.load_by_school_and_year(school_natural_id, academic_year_natural_id)
        period_natural_id = self._period_id_factory()
        normalized_fields = PeriodFields(
            name=name, start_time=fields.start_time, end_time=fields.end_time,
            starts_new_block=fields.starts_new_block,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            candidate = current_problem.periods + (
                Period(
                    id=period_natural_id, name=name, index=len(current_problem.periods), block_id="",
                    is_instructional=True, start_time=fields.start_time, end_time=fields.end_time,
                ),
            )
            rules.validate_period_create(
                current_problem, school_natural_id, academic_year_natural_id, name=name,
                start_time=fields.start_time, end_time=fields.end_time, candidate_periods=candidate,
            )

        validate(problem)

        return self._period_repository.create(
            school_natural_id, academic_year_natural_id, period_natural_id, normalized_fields, validate=validate,
        )

    def period_update(
        self, school_natural_id: str, academic_year_natural_id: str, period_id: str, fields: PeriodFields,
    ) -> PeriodWriteResult:
        name = rules.normalize_name(fields.name)
        problem = self._problem_repository.load_by_school_and_year(school_natural_id, academic_year_natural_id)
        normalized_fields = PeriodFields(
            name=name, start_time=fields.start_time, end_time=fields.end_time,
            starts_new_block=fields.starts_new_block,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            candidate = tuple(
                replace(p, name=name, start_time=fields.start_time, end_time=fields.end_time)
                if p.id == period_id else p
                for p in current_problem.periods
            )
            rules.validate_period_update(
                current_problem, school_natural_id, academic_year_natural_id, period_id, name=name,
                start_time=fields.start_time, end_time=fields.end_time, candidate_periods=candidate,
            )

        validate(problem)

        return self._period_repository.update(
            school_natural_id, academic_year_natural_id, period_id, normalized_fields, validate=validate,
        )

    def period_delete(self, school_natural_id: str, academic_year_natural_id: str, period_id: str) -> None:
        problem = self._problem_repository.load_by_school_and_year(school_natural_id, academic_year_natural_id)

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_period_delete(current_problem, school_natural_id, academic_year_natural_id, period_id)

        validate(problem)

        self._period_repository.delete(school_natural_id, academic_year_natural_id, period_id, validate=validate)

    def period_move(
        self, school_natural_id: str, academic_year_natural_id: str, period_id: str, direction: str,
    ) -> None:
        problem = self._problem_repository.load_by_school_and_year(school_natural_id, academic_year_natural_id)

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_period_move(
                current_problem, school_natural_id, academic_year_natural_id, period_id, direction,
            )

        validate(problem)

        self._period_repository.move(
            school_natural_id, academic_year_natural_id, period_id, direction, validate=validate,
        )
