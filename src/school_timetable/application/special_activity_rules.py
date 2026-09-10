"""Pure, framework-free business rules for the Reserved Activities
Slice A1 Special Activity write service.

Every function here reasons only about already-loaded, plain
`SchedulingProblem`/domain objects -- never SQLAlchemy, never a
persistence surrogate ID. This is deliberate: the same rule functions
run twice per write (an early, un-locked fast-fail check in
`SpecialActivityService`, and the authoritative, lock-protected
recheck `SqlAlchemySpecialActivityRepository` invokes against a
freshly-reloaded `SchedulingProblem` immediately before committing --
Owner Decision #36) -- reusing one pure implementation for both means
the two checks can never silently diverge.

"Special Activity" is not a new domain entity -- it is the user-facing
name for `Activity(kind=CLUB)` (see `domain/activities.py`), mirroring
"Subject"'s own relationship to `Activity(kind=ORDINARY)`
(`subject_rules.py`). Every function here therefore only ever
finds/validates `CLUB` activities; `ActivityKind.ORDINARY` activities
are treated as if they do not exist on this surface at all.
"""
from __future__ import annotations

from school_timetable.application.errors import (
    DuplicateSpecialActivityError,
    InvalidSpecialActivityError,
    SpecialActivityInUseError,
    SpecialActivityNotFoundError,
)
from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.validation.errors import ValidationError


def find_special_activity(problem: SchedulingProblem, special_activity_id: str) -> Activity | None:
    """The `ActivityKind.CLUB` activity with this ID, or `None` --
    identically whether `special_activity_id` does not exist at all or
    it exists but is `ActivityKind.ORDINARY`. An ORDINARY activity is
    never returned here, matching the filtered-resource-surface
    contract (mirrors `subject_rules.find_ordinary_subject`): it must
    be indistinguishable from a missing Special Activity."""
    activity = next((a for a in problem.activities if a.id == special_activity_id), None)
    if activity is None or activity.kind != ActivityKind.CLUB:
        return None
    return activity


def find_duplicate(
    problem: SchedulingProblem, name: str, *, exclude_special_activity_id: str | None = None,
) -> Activity | None:
    """The first existing `CLUB` activity (other than
    `exclude_special_activity_id`, for update) sharing the identical,
    exact, case-sensitive, trimmed `name`, or `None`. Never checks
    against `ORDINARY` activities -- an identically-named Subject is
    never a conflict."""
    for activity in problem.activities:
        if activity.kind != ActivityKind.CLUB:
            continue
        if activity.id == exclude_special_activity_id:
            continue
        if activity.name == name:
            return activity
    return None


def normalize_name(name: str) -> str:
    """Trims outer whitespace only -- never restricts alphabet, script,
    punctuation, or length (Unicode/Georgian names, ampersands, and
    internal spaces are all valid)."""
    return name.strip()


def validate_name(school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
    """Raises `InvalidSpecialActivityError` if the normalized name is
    blank."""
    if name == "":
        raise InvalidSpecialActivityError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("BLANK_SPECIAL_ACTIVITY_NAME", "name is blank after trimming"),),
        )


def find_special_activity_references(problem: SchedulingProblem, special_activity_id: str) -> tuple[str, ...]:
    """Every current-configuration entity kind referencing
    `special_activity_id` (the underlying `Activity`'s natural ID) --
    only ever `RESERVED_BLOCK`, the sole direct `activity_id`
    reference a CLUB activity can have in the persisted schema (a CLUB
    activity is never a `TeachingRequirement` target). Historical
    `ScheduleVersion`/`ScheduleEntry` rows are never inspected here --
    Decision #35 already forbids reaching this check at all once any
    `Schedule` exists for the year."""
    kinds: list[str] = []
    if any(b.activity_id == special_activity_id for b in problem.reserved_blocks):
        kinds.append("RESERVED_BLOCK")
    return tuple(kinds)


def validate_create(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    *,
    name: str,
) -> None:
    validate_name(school_natural_id, academic_year_natural_id, name)
    duplicate = find_duplicate(problem, name)
    if duplicate is not None:
        raise DuplicateSpecialActivityError(school_natural_id, academic_year_natural_id, name)


def validate_update(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    special_activity_id: str,
    *,
    name: str,
) -> None:
    if find_special_activity(problem, special_activity_id) is None:
        raise SpecialActivityNotFoundError(school_natural_id, academic_year_natural_id, special_activity_id)
    validate_name(school_natural_id, academic_year_natural_id, name)
    duplicate = find_duplicate(problem, name, exclude_special_activity_id=special_activity_id)
    if duplicate is not None:
        raise DuplicateSpecialActivityError(school_natural_id, academic_year_natural_id, name)


def validate_delete(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    special_activity_id: str,
) -> None:
    if find_special_activity(problem, special_activity_id) is None:
        raise SpecialActivityNotFoundError(school_natural_id, academic_year_natural_id, special_activity_id)
    blocking = find_special_activity_references(problem, special_activity_id)
    if blocking:
        raise SpecialActivityInUseError(school_natural_id, academic_year_natural_id, special_activity_id, blocking)
