"""Pure, framework-free business rules for the Real-School Setup MVP
Slice B Teacher write service.

Every function here reasons only about already-loaded, plain
`SchedulingProblem`/domain objects -- never SQLAlchemy, never a
persistence surrogate ID. This is deliberate: the same rule functions
run twice per write (an early, un-locked fast-fail check in
`TeacherService`, and the authoritative, lock-protected recheck
`SqlAlchemyTeacherRepository` invokes against a freshly-reloaded
`SchedulingProblem` immediately before committing -- Owner Decision
#36) -- reusing one pure implementation for both means the two checks
can never silently diverge.
"""
from __future__ import annotations

from school_timetable.application.errors import InvalidTeacherError, TeacherInUseError, TeacherNotFoundError
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.validation.errors import ValidationError


def find_teacher(problem: SchedulingProblem, teacher_id: str) -> Teacher | None:
    return next((t for t in problem.teachers if t.id == teacher_id), None)


def normalize_fields(first_name: str, last_name: str) -> tuple[str, str]:
    """Trims outer whitespace only -- never restricts alphabet, script,
    punctuation, or length (Unicode/Georgian names, apostrophes,
    hyphens, and internal spaces are all valid)."""
    return first_name.strip(), last_name.strip()


def validate_fields(
    school_natural_id: str,
    academic_year_natural_id: str,
    first_name: str,
    last_name: str,
) -> None:
    """Raises `InvalidTeacherError` if either normalized field is blank.
    Applies only to new create/update input -- never re-run merely to
    read an existing, already-migrated row (a legacy row's explicit
    `last_name=""` remains readable, per Owner Decision #37)."""
    errors: list[ValidationError] = []
    if first_name == "":
        errors.append(ValidationError("BLANK_FIRST_NAME", "first_name is blank after trimming"))
    if last_name == "":
        errors.append(ValidationError("BLANK_LAST_NAME", "last_name is blank after trimming"))
    if errors:
        raise InvalidTeacherError(school_natural_id, academic_year_natural_id, tuple(errors))


def referenced_by(problem: SchedulingProblem, teacher_id: str) -> tuple[str, ...]:
    """Every current-configuration entity kind referencing `teacher_id`,
    in the deterministic order `TEACHING_REQUIREMENT`,
    `TEACHER_AVAILABILITY`, `RESERVED_BLOCK` -- only the kinds that
    actually reference it. `FixedPlacement` needs no separate check: it
    references a `TeachingRequirement`, never a teacher directly, so any
    teacher it indirectly touches is already caught by the
    `TEACHING_REQUIREMENT` check. Historical `ScheduleVersion`/
    `ScheduleEntry` rows are never inspected here -- Decision #35
    already forbids reaching this check at all once any `Schedule`
    exists for the year."""
    kinds: list[str] = []
    if any(r.teacher_id == teacher_id for r in problem.teaching_requirements):
        kinds.append("TEACHING_REQUIREMENT")
    if any(a.teacher_id == teacher_id for a in problem.teacher_availabilities):
        kinds.append("TEACHER_AVAILABILITY")
    if any(b.teacher_id == teacher_id for b in problem.reserved_blocks):
        kinds.append("RESERVED_BLOCK")
    return tuple(kinds)


def validate_create(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    *,
    first_name: str,
    last_name: str,
) -> None:
    """Same-name teachers are always allowed -- no uniqueness check on
    `first_name`/`last_name` (Owner Decision #37)."""
    validate_fields(school_natural_id, academic_year_natural_id, first_name, last_name)


def validate_update(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    teacher_id: str,
    *,
    first_name: str,
    last_name: str,
) -> None:
    if find_teacher(problem, teacher_id) is None:
        raise TeacherNotFoundError(school_natural_id, academic_year_natural_id, teacher_id)
    validate_fields(school_natural_id, academic_year_natural_id, first_name, last_name)


def validate_delete(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    teacher_id: str,
) -> None:
    if find_teacher(problem, teacher_id) is None:
        raise TeacherNotFoundError(school_natural_id, academic_year_natural_id, teacher_id)
    blocking = referenced_by(problem, teacher_id)
    if blocking:
        raise TeacherInUseError(school_natural_id, academic_year_natural_id, teacher_id, blocking)
