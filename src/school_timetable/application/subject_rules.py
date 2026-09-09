"""Pure, framework-free business rules for the Real-School Setup MVP
Slice D Subject write service.

Every function here reasons only about already-loaded, plain
`SchedulingProblem`/domain objects -- never SQLAlchemy, never a
persistence surrogate ID. This is deliberate: the same rule functions
run twice per write (an early, un-locked fast-fail check in
`SubjectService`, and the authoritative, lock-protected recheck
`SqlAlchemyActivityRepository` invokes against a freshly-reloaded
`SchedulingProblem` immediately before committing -- Owner Decision
#36) -- reusing one pure implementation for both means the two checks
can never silently diverge.

"Subject" is not a new domain entity -- it is the user-facing name for
`Activity(kind=ORDINARY)` (see `domain/activities.py`). Every function
here therefore only ever finds/validates `ORDINARY` activities;
`ActivityKind.CLUB` activities are treated as if they do not exist on
this surface at all.
"""
from __future__ import annotations

from school_timetable.application.errors import (
    DuplicateSubjectError,
    InvalidSubjectError,
    SubjectInUseError,
    SubjectNotFoundError,
)
from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.validation.errors import ValidationError


def find_ordinary_subject(problem: SchedulingProblem, subject_id: str) -> Activity | None:
    """The `ActivityKind.ORDINARY` activity with this ID, or `None` --
    identically whether `subject_id` does not exist at all or it exists
    but is `ActivityKind.CLUB`. A CLUB activity is never returned here,
    matching the filtered-resource-surface contract (Slice D design
    gate section 8): it must be indistinguishable from a missing
    Subject."""
    activity = next((a for a in problem.activities if a.id == subject_id), None)
    if activity is None or activity.kind != ActivityKind.ORDINARY:
        return None
    return activity


def find_duplicate(
    problem: SchedulingProblem, name: str, *, exclude_subject_id: str | None = None,
) -> Activity | None:
    """The first existing `ORDINARY` activity (other than
    `exclude_subject_id`, for update) sharing the identical, exact,
    case-sensitive, trimmed `name`, or `None`. Never checks against
    `CLUB` activities -- an identically-named club is never a conflict."""
    for activity in problem.activities:
        if activity.kind != ActivityKind.ORDINARY:
            continue
        if activity.id == exclude_subject_id:
            continue
        if activity.name == name:
            return activity
    return None


def normalize_name(name: str) -> str:
    """Trims outer whitespace only -- never restricts alphabet, script,
    punctuation, or length (Unicode/Georgian subject names, ampersands,
    and internal spaces are all valid)."""
    return name.strip()


def validate_name(school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
    """Raises `InvalidSubjectError` if the normalized name is blank."""
    if name == "":
        raise InvalidSubjectError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("BLANK_SUBJECT_NAME", "name is blank after trimming"),),
        )


def referenced_by(problem: SchedulingProblem, subject_id: str) -> tuple[str, ...]:
    """Every current-configuration entity kind referencing `subject_id`
    (the underlying `Activity`'s natural ID), in the deterministic order
    `TEACHING_REQUIREMENT`, `RESERVED_BLOCK` -- the only two direct
    `activity_id` references in the persisted schema; only the kinds
    that actually reference it. Even though the repaired Teaching
    Assignment contract now means only `ORDINARY` activities can ever
    become new `TeachingRequirement` targets, and `ReservedBlock`
    conceptually belongs to `CLUB` activities, existing/imported/
    test-created data may still directly reference an `ORDINARY`
    activity from a `ReservedBlock` -- any such direct reference blocks
    deletion regardless. `FixedPlacement`/`ResourceRequirement` are
    never direct `Activity` references (confirmed against the persisted
    schema) and are deliberately not separate blocker kinds. Historical
    `ScheduleVersion`/`ScheduleEntry` rows are never inspected here --
    Decision #35 already forbids reaching this check at all once any
    `Schedule` exists for the year."""
    kinds: list[str] = []
    if any(r.activity_id == subject_id for r in problem.teaching_requirements):
        kinds.append("TEACHING_REQUIREMENT")
    if any(b.activity_id == subject_id for b in problem.reserved_blocks):
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
        raise DuplicateSubjectError(school_natural_id, academic_year_natural_id, name)


def validate_update(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    subject_id: str,
    *,
    name: str,
) -> None:
    if find_ordinary_subject(problem, subject_id) is None:
        raise SubjectNotFoundError(school_natural_id, academic_year_natural_id, subject_id)
    validate_name(school_natural_id, academic_year_natural_id, name)
    duplicate = find_duplicate(problem, name, exclude_subject_id=subject_id)
    if duplicate is not None:
        raise DuplicateSubjectError(school_natural_id, academic_year_natural_id, name)


def validate_delete(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    subject_id: str,
) -> None:
    if find_ordinary_subject(problem, subject_id) is None:
        raise SubjectNotFoundError(school_natural_id, academic_year_natural_id, subject_id)
    blocking = referenced_by(problem, subject_id)
    if blocking:
        raise SubjectInUseError(school_natural_id, academic_year_natural_id, subject_id, blocking)
