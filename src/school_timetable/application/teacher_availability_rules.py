"""Pure, framework-free business rules for the Teacher Availability
write use case (Owner Decision #38).

Every function here reasons only about already-loaded, plain
`SchedulingProblem`/domain objects -- never SQLAlchemy, never a
persistence surrogate ID. This is deliberate: the same pure
`validate_replace` runs twice per write (an early, un-locked fast-fail
check in `TeacherAvailabilityService`, and the authoritative,
lock-protected recheck `SqlAlchemyTeacherAvailabilityRepository`
invokes against a freshly-reloaded `SchedulingProblem` immediately
before committing -- Owner Decision #36) -- reusing one pure
implementation for both means the two checks can never silently
diverge, matching `teacher_rules.py`'s exact discipline.

The write unit is the complete desired sparse exception set for one
Teacher (Owner Decision #38's locked write contract): `AVAILABLE` is
never a valid member of a submitted exception set -- it is
represented purely by a cell's absence, so an explicit `AVAILABLE`
entry is rejected rather than silently normalized away, keeping the
sparse contract unambiguous.
"""
from __future__ import annotations

from school_timetable.application.errors import (
    InvalidTeacherAvailabilityError,
    TeacherNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.teacher_availability_models import TeacherAvailabilityExceptionFields
from school_timetable.domain.people import AvailabilityStatus
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.validation.errors import ValidationError

_VALID_EXCEPTION_STATUSES = frozenset({AvailabilityStatus.PREFER_NOT.value, AvailabilityStatus.UNAVAILABLE.value})


def find_teacher_id(problem: SchedulingProblem, teacher_id: str) -> bool:
    return any(t.id == teacher_id for t in problem.teachers)


def validate_replace(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    teacher_id: str,
    *,
    exceptions: tuple[TeacherAvailabilityExceptionFields, ...],
) -> None:
    """Raises `TeacherNotFoundError` if `teacher_id` does not exist;
    then `InvalidTeacherAvailabilityError` (carrying every structural
    diagnostic found, never just the first) for any explicit
    `AVAILABLE` entry, any unrecognized status string, or any
    duplicated `(day_id, period_id)` cell within `exceptions` --
    structural checks never depend on a DB lookup, so they are all
    collected before the reference-existence checks below, which do.
    Finally raises `UnknownReferenceError` for the first entry (in
    request order) whose `day_id`/`period_id` does not exist in this
    academic year's configuration."""
    if not find_teacher_id(problem, teacher_id):
        raise TeacherNotFoundError(school_natural_id, academic_year_natural_id, teacher_id)

    errors: list[ValidationError] = []
    seen_cells: set[tuple[str, str]] = set()
    duplicate_cells: set[tuple[str, str]] = set()
    for entry in exceptions:
        cell = (entry.day_id, entry.period_id)
        if cell in seen_cells:
            duplicate_cells.add(cell)
        seen_cells.add(cell)

        if entry.status == AvailabilityStatus.AVAILABLE.value:
            errors.append(ValidationError(
                "AVAILABLE_EXCEPTION_MUST_BE_OMITTED",
                f"AVAILABLE cell (day={entry.day_id!r}, period={entry.period_id!r}) must be omitted, "
                "never stated explicitly -- it is the sparse default for any cell with no exception row",
                {"day_id": entry.day_id, "period_id": entry.period_id},
            ))
        elif entry.status not in _VALID_EXCEPTION_STATUSES:
            errors.append(ValidationError(
                "UNKNOWN_AVAILABILITY_STATUS",
                f"unrecognized availability status {entry.status!r} for cell "
                f"(day={entry.day_id!r}, period={entry.period_id!r})",
                {"day_id": entry.day_id, "period_id": entry.period_id, "status": entry.status},
            ))

    for day_id, period_id in sorted(duplicate_cells):
        errors.append(ValidationError(
            "DUPLICATE_AVAILABILITY_CELL",
            f"cell (day={day_id!r}, period={period_id!r}) appears more than once in this request",
            {"day_id": day_id, "period_id": period_id},
        ))

    if errors:
        raise InvalidTeacherAvailabilityError(school_natural_id, academic_year_natural_id, tuple(errors))

    for entry in exceptions:
        if not any(d.id == entry.day_id for d in problem.days):
            raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "day", entry.day_id)
        if not any(p.id == entry.period_id for p in problem.periods):
            raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "period", entry.period_id)
