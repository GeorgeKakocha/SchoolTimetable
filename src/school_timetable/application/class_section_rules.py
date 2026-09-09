"""Pure, framework-free business rules for the Real-School Setup MVP
Slice C Class write service.

Every function here reasons only about already-loaded, plain
`SchedulingProblem`/domain objects -- never SQLAlchemy, never a
persistence surrogate ID. This is deliberate: the same rule functions
run twice per write (an early, un-locked fast-fail check in
`ClassSectionService`, and the authoritative, lock-protected recheck
`SqlAlchemyClassSectionRepository` invokes against a freshly-reloaded
`SchedulingProblem` immediately before committing -- Owner Decision
#36) -- reusing one pure implementation for both means the two checks
can never silently diverge.
"""
from __future__ import annotations

from school_timetable.application.errors import (
    ClassSectionInUseError,
    ClassSectionNotFoundError,
    DuplicateClassError,
    InvalidClassError,
)
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.validation.errors import ValidationError


class CanonicalWholeClassGroupInvariantError(Exception):
    """INTERNAL defect only -- never a public application outcome, never
    given an HTTP mapping (matches `errors.py`'s own stated philosophy
    for internal defects). Raised when a `ClassSection` has zero or
    more than one canonical `WHOLE_CLASS` `ParticipantGroup` -- a
    configuration state Class CRUD's own atomic create/delete
    transactions must never produce, so reaching this means a genuine
    internal invariant violation (Owner Decision #33), not a
    user-actionable outcome. Never silently repaired, never mapped to
    404/409 -- left to surface as a generic 500."""

    def __init__(self, class_id: str, matching_group_ids: tuple[str, ...]) -> None:
        self.class_id = class_id
        self.matching_group_ids = matching_group_ids
        super().__init__(
            f"class {class_id!r} has {len(matching_group_ids)} canonical WHOLE_CLASS "
            f"participant groups (expected exactly 1): {list(matching_group_ids)!r}"
        )


def find_class(problem: SchedulingProblem, class_id: str) -> ClassSection | None:
    return next((c for c in problem.class_sections if c.id == class_id), None)


def find_duplicate(
    problem: SchedulingProblem, name: str, *, exclude_class_id: str | None = None,
) -> ClassSection | None:
    """The first existing class (other than `exclude_class_id`, for
    update) sharing the identical, exact, case-sensitive, trimmed
    `name`, or `None`."""
    for class_section in problem.class_sections:
        if class_section.id == exclude_class_id:
            continue
        if class_section.name == name:
            return class_section
    return None


def resolve_canonical_whole_class_group(problem: SchedulingProblem, class_id: str) -> ParticipantGroup:
    """The one authoritative canonical `WHOLE_CLASS` group for
    `class_id` -- resolved ONLY by `role == WHOLE_CLASS` and
    `class_sections == (class_id,)`, exactly matching
    `teaching_assignments_projection_service._whole_class_target`'s own
    predicate. Never inferred by name, ID prefix, position, or first
    match. Zero or more than one match is a
    `CanonicalWholeClassGroupInvariantError` -- never silently repaired,
    never a chosen-arbitrarily fallback."""
    matches = [
        g for g in problem.participant_groups
        if g.role == ParticipantGroupRole.WHOLE_CLASS and g.class_sections == (class_id,)
    ]
    if len(matches) != 1:
        raise CanonicalWholeClassGroupInvariantError(class_id, tuple(g.id for g in matches))
    return matches[0]


def normalize_name(name: str) -> str:
    """Trims outer whitespace only -- never restricts alphabet, script,
    punctuation, or length (Unicode/Georgian class names, hyphens, and
    internal spaces are all valid)."""
    return name.strip()


def validate_name(school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
    """Raises `InvalidClassError` if the normalized name is blank."""
    if name == "":
        raise InvalidClassError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("BLANK_CLASS_NAME", "name is blank after trimming"),),
        )


def referenced_by(problem: SchedulingProblem, class_id: str, canonical_group_id: str) -> tuple[str, ...]:
    """Every current-configuration entity kind referencing `class_id`
    (directly) or its canonical WHOLE_CLASS group (indirectly), in the
    deterministic order `TEACHING_REQUIREMENT`, `RESERVED_BLOCK`,
    `SUBGROUP`, `MERGED_CLASSES` -- only the kinds that actually
    reference it. `FixedPlacement` needs no separate check: it
    references a `TeachingRequirement`, never a class or group
    directly, so any class it indirectly touches is already caught by
    the `TEACHING_REQUIREMENT` check. Historical `ScheduleVersion`/
    `ScheduleEntry` rows are never inspected here -- Decision #35
    already forbids reaching this check at all once any `Schedule`
    exists for the year."""
    kinds: list[str] = []
    if any(r.participant_group_id == canonical_group_id for r in problem.teaching_requirements):
        kinds.append("TEACHING_REQUIREMENT")
    if any(class_id in b.class_sections for b in problem.reserved_blocks):
        kinds.append("RESERVED_BLOCK")
    if any(
        g.role == ParticipantGroupRole.SUBGROUP and class_id in g.class_sections
        for g in problem.participant_groups
    ):
        kinds.append("SUBGROUP")
    if any(
        g.role == ParticipantGroupRole.MERGED_CLASSES and class_id in g.class_sections
        for g in problem.participant_groups
    ):
        kinds.append("MERGED_CLASSES")
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
        raise DuplicateClassError(school_natural_id, academic_year_natural_id, name)


def validate_update(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    class_id: str,
    *,
    name: str,
) -> None:
    if find_class(problem, class_id) is None:
        raise ClassSectionNotFoundError(school_natural_id, academic_year_natural_id, class_id)
    validate_name(school_natural_id, academic_year_natural_id, name)
    duplicate = find_duplicate(problem, name, exclude_class_id=class_id)
    if duplicate is not None:
        raise DuplicateClassError(school_natural_id, academic_year_natural_id, name)
    # Confirm the canonical invariant holds before any mutation is
    # attempted -- raises CanonicalWholeClassGroupInvariantError (an
    # internal defect) rather than silently proceeding.
    resolve_canonical_whole_class_group(problem, class_id)


def validate_delete(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    class_id: str,
) -> None:
    if find_class(problem, class_id) is None:
        raise ClassSectionNotFoundError(school_natural_id, academic_year_natural_id, class_id)
    canonical_group = resolve_canonical_whole_class_group(problem, class_id)
    blocking = referenced_by(problem, class_id, canonical_group.id)
    if blocking:
        raise ClassSectionInUseError(school_natural_id, academic_year_natural_id, class_id, blocking)
