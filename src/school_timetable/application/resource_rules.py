"""Pure, framework-free business rules for the Resources Slice A
Resource catalog write service.

Every function here reasons only about already-loaded, plain
`SchedulingProblem`/domain objects -- never SQLAlchemy, never a
persistence surrogate ID. This is deliberate: the same rule functions
run twice per write (an early, un-locked fast-fail check in
`ResourceService`, and the authoritative, lock-protected recheck
`SqlAlchemyResourceRepository` invokes against a freshly-reloaded
`SchedulingProblem` immediately before committing -- Owner Decision
#36) -- reusing one pure implementation for both means the two checks
can never silently diverge.

`Resource` is the existing `domain.resources.Resource(id, name,
capacity=1)` -- not a new/redesigned entity. `capacity` means "maximum
number of simultaneous lesson/resource occupations," never
student-seat/room-headcount capacity (that would be a different,
separately-named future field, never this one).
"""
from __future__ import annotations

from school_timetable.application.errors import (
    DuplicateResourceError,
    InvalidResourceError,
    ResourceInUseError,
    ResourceNotFoundError,
)
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.resources import Resource
from school_timetable.validation.errors import ValidationError


def find_resource(problem: SchedulingProblem, resource_id: str) -> Resource | None:
    return next((r for r in problem.resources if r.id == resource_id), None)


def find_duplicate(
    problem: SchedulingProblem, name: str, *, exclude_resource_id: str | None = None,
) -> Resource | None:
    """The first existing `Resource` (other than `exclude_resource_id`,
    for update) sharing the identical, exact, case-sensitive, trimmed
    `name`, or `None`. Never checks against any other catalog (Subject/
    Teacher/Class/Special Activity) -- Resource is its own separate
    catalog."""
    for resource in problem.resources:
        if resource.id == exclude_resource_id:
            continue
        if resource.name == name:
            return resource
    return None


def normalize_name(name: str) -> str:
    """Trims outer whitespace only -- never restricts alphabet, script,
    punctuation, or length (Unicode/Georgian names, ampersands, and
    internal spaces are all valid), matching every other catalog's
    `normalize_name`."""
    return name.strip()


def validate_name(school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
    """Raises `InvalidResourceError` if the normalized name is blank."""
    if name == "":
        raise InvalidResourceError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("BLANK_RESOURCE_NAME", "name is blank after trimming"),),
        )


def validate_capacity(school_natural_id: str, academic_year_natural_id: str, capacity: int) -> None:
    """Raises `InvalidResourceError` if `capacity` is not a positive
    integer. The database's own `CheckConstraint("capacity > 0")`
    remains a structural backstop only -- this application-layer check
    is what turns an invalid capacity into a safe, structured 422
    response instead of a raw database integrity failure reaching the
    caller."""
    if capacity < 1:
        raise InvalidResourceError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("INVALID_RESOURCE_CAPACITY", "capacity must be at least 1"),),
        )


def find_resource_references(problem: SchedulingProblem, resource_id: str) -> tuple[str, ...]:
    """Every current-configuration entity kind referencing
    `resource_id`, in deterministic order: `TEACHING_REQUIREMENT`
    (`TeachingRequirement.resource_requirement.resource_id`), then
    `RESERVED_BLOCK` (`ReservedBlock.resource_id`, Resources B2).
    Historical `ScheduleVersion`/`ScheduleEntry` rows are never
    inspected here -- Decision #35 already forbids reaching this check
    at all once any `Schedule` exists for the year."""
    kinds: list[str] = []
    if any(
        req.resource_requirement is not None and req.resource_requirement.resource_id == resource_id
        for req in problem.teaching_requirements
    ):
        kinds.append("TEACHING_REQUIREMENT")
    if any(block.resource_id == resource_id for block in problem.reserved_blocks):
        kinds.append("RESERVED_BLOCK")
    return tuple(kinds)


def validate_create(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    *,
    name: str,
    capacity: int,
) -> None:
    validate_name(school_natural_id, academic_year_natural_id, name)
    validate_capacity(school_natural_id, academic_year_natural_id, capacity)
    duplicate = find_duplicate(problem, name)
    if duplicate is not None:
        raise DuplicateResourceError(school_natural_id, academic_year_natural_id, name)


def validate_update(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    resource_id: str,
    *,
    name: str,
    capacity: int,
) -> None:
    if find_resource(problem, resource_id) is None:
        raise ResourceNotFoundError(school_natural_id, academic_year_natural_id, resource_id)
    validate_name(school_natural_id, academic_year_natural_id, name)
    validate_capacity(school_natural_id, academic_year_natural_id, capacity)
    duplicate = find_duplicate(problem, name, exclude_resource_id=resource_id)
    if duplicate is not None:
        raise DuplicateResourceError(school_natural_id, academic_year_natural_id, name)


def validate_delete(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    resource_id: str,
) -> None:
    if find_resource(problem, resource_id) is None:
        raise ResourceNotFoundError(school_natural_id, academic_year_natural_id, resource_id)
    blocking = find_resource_references(problem, resource_id)
    if blocking:
        raise ResourceInUseError(school_natural_id, academic_year_natural_id, resource_id, blocking)
