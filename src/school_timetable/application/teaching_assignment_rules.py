"""Pure, framework-free business rules for Phase 3C.2's narrow Teaching
Assignment write service (`docs/DECISIONS.md` #34, #36).

Every function here reasons only about already-loaded, plain
`SchedulingProblem`/domain objects -- never SQLAlchemy, never a
persistence surrogate ID. This is deliberate: the same rule functions
run twice per write (an early, un-locked fast-fail check in
`TeachingAssignmentService`, and the authoritative, lock-protected
recheck `SqlAlchemyTeachingAssignmentRepository` invokes against a
freshly-reloaded `SchedulingProblem` immediately before committing --
see Owner Decision #36) -- reusing one pure implementation for both
means the two checks can never silently diverge.
"""
from __future__ import annotations

from dataclasses import replace

from school_timetable.application import resource_rules
from school_timetable.application.errors import (
    AdvancedRequirementNotEditableError,
    DuplicateTeachingAssignmentError,
    InvalidTeachingAssignmentError,
    NonOrdinaryActivityTargetError,
    NonWholeClassTargetError,
    TeachingAssignmentNotFoundError,
    UnknownReferenceError,
)
from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.groups import ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    DistributionPolicy,
    LessonBlockPolicy,
    TeachingRequirement,
)
from school_timetable.domain.resources import ResourceRequirement
from school_timetable.validation.errors import ValidationError
from school_timetable.validation.preflight import run_preflight

_PLAIN_BLOCK_POLICY = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)
_PLAIN_DISTRIBUTION_POLICY = DistributionPolicy()

# Preflight codes that reflect a school's ordinary, expected
# mid-configuration incompleteness (Owner Decision #34's save-time
# validation boundary) -- these never block a save, only surface as
# warnings; every other preflight code newly introduced by a mutation
# blocks it outright.
WARNING_ONLY_VALIDATION_CODES = frozenset({"TEACHER_OVERLOADED", "CLASS_OCCUPANCY_MISMATCH"})


def find_participant_group(problem: SchedulingProblem, participant_group_id: str) -> ParticipantGroup | None:
    return next((g for g in problem.participant_groups if g.id == participant_group_id), None)


def find_activity(problem: SchedulingProblem, activity_id: str) -> Activity | None:
    return next((a for a in problem.activities if a.id == activity_id), None)


def require_ordinary_activity(
    problem: SchedulingProblem, school_natural_id: str, academic_year_natural_id: str, activity_id: str,
) -> None:
    """Shared by `validate_create`/`validate_update`: raises
    `UnknownReferenceError` if `activity_id` does not exist at all, or
    `NonOrdinaryActivityTargetError` if it exists but is not
    `ActivityKind.ORDINARY` (pre-Slice-D correction -- `CLUB` activities
    are scheduled via `ReservedBlock`, never a `TeachingRequirement`)."""
    activity = find_activity(problem, activity_id)
    if activity is None:
        raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "activity", activity_id)
    if activity.kind != ActivityKind.ORDINARY:
        raise NonOrdinaryActivityTargetError(
            school_natural_id, academic_year_natural_id, activity_id, activity.kind.value,
        )


def require_known_resource(
    problem: SchedulingProblem, school_natural_id: str, academic_year_natural_id: str, resource_id: str,
) -> None:
    """Raises `UnknownReferenceError` if `resource_id` does not resolve
    in this school/academic-year's persisted Resource catalog -- reuses
    `resource_rules.find_resource` (Resources Slice A) rather than a
    second lookup implementation. Scoping to the already-loaded,
    year-specific `problem.resources` is what makes a cross-AY Resource
    natural ID behave identically to a genuinely unknown one (Resources
    B1's cross-AY defense-in-depth)."""
    if resource_rules.find_resource(problem, resource_id) is None:
        raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "resource", resource_id)


def find_requirement(problem: SchedulingProblem, natural_id: str) -> TeachingRequirement | None:
    return next((r for r in problem.teaching_requirements if r.id == natural_id), None)


def find_duplicate(
    problem: SchedulingProblem,
    teacher_id: str,
    participant_group_id: str,
    activity_id: str,
    *,
    exclude_natural_id: str | None = None,
) -> TeachingRequirement | None:
    """The first existing requirement (other than `exclude_natural_id`,
    for update) sharing the identical `(teacher, participant_group,
    activity)` triple, or `None`."""
    for requirement in problem.teaching_requirements:
        if requirement.id == exclude_natural_id:
            continue
        if (
            requirement.teacher_id == teacher_id
            and requirement.participant_group_id == participant_group_id
            and requirement.activity_id == activity_id
        ):
            return requirement
    return None


def plain_reasons(problem: SchedulingProblem, requirement: TeachingRequirement) -> tuple[str, ...]:
    """Every advanced feature found on `requirement` -- empty when it is
    "plain" (Phase 3C.2's narrow editable predicate). Never inferred
    from names; each check inspects the requirement's own fields, its
    target `ParticipantGroup.role`, or (for `FixedPlacement`, a separate
    domain object) the problem's `fixed_placements`."""
    reasons: list[str] = []

    group = find_participant_group(problem, requirement.participant_group_id)
    if group is None or group.role != ParticipantGroupRole.WHOLE_CLASS:
        reasons.append("participant_group_role")
    if requirement.split_group_id is not None:
        reasons.append("split_group_id")
    if requirement.block_policy != _PLAIN_BLOCK_POLICY:
        reasons.append("block_policy")
    if requirement.distribution_policy != _PLAIN_DISTRIBUTION_POLICY:
        reasons.append("distribution_policy")
    if requirement.time_preferences != ():
        reasons.append("time_preferences")
    # Resources B1 (Option A): a fixed `resource_requirement` is no
    # longer an Advanced disqualifier on its own -- "this lesson always
    # happens in the gym" is an ordinary, plain-editable attribute now
    # that `TeachingAssignmentFields.resource_id` can express it
    # directly. An otherwise-plain, resource-bearing requirement is
    # therefore editable/deletable through this same narrow write
    # service; every other advanced reason below is unaffected.
    if any(fp.requirement_id == requirement.id for fp in problem.fixed_placements):
        reasons.append("fixed_placement")

    return tuple(reasons)


def is_plain(problem: SchedulingProblem, requirement: TeachingRequirement) -> bool:
    return plain_reasons(problem, requirement) == ()


def new_validation_errors(
    baseline: SchedulingProblem, candidate: SchedulingProblem,
) -> tuple[ValidationError, ...]:
    """Every `ValidationError` code preflight reports against `candidate`
    but not against `baseline` -- pre-existing, unrelated configuration
    problems (e.g. a different class already under-occupied) never
    block an otherwise-valid save (Owner Decision #34's save-time
    validation boundary)."""
    baseline_codes = {e.code for e in run_preflight(baseline)}
    return tuple(e for e in run_preflight(candidate) if e.code not in baseline_codes)


def split_blocking_and_warnings(
    new_errors: tuple[ValidationError, ...],
) -> tuple[tuple[ValidationError, ...], tuple[ValidationError, ...]]:
    blocking = tuple(e for e in new_errors if e.code not in WARNING_ONLY_VALIDATION_CODES)
    warnings = tuple(e for e in new_errors if e.code in WARNING_ONLY_VALIDATION_CODES)
    return blocking, warnings


def validate_create(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    *,
    teacher_id: str,
    participant_group_id: str,
    activity_id: str,
    weekly_periods: int,
    resource_id: str | None = None,
) -> tuple[ValidationError, ...]:
    """Validates a create request against `problem` (a specific,
    already-loaded snapshot -- the caller is responsible for supplying
    either an initial fast-fail snapshot or, authoritatively, a
    freshly-reloaded one under the Owner-Decision-#36 lock). Raises on
    any hard-blocking violation; returns non-blocking warnings
    (`WARNING_ONLY_VALIDATION_CODES`) for the caller to surface.
    `resource_id=None` means "no fixed Resource" (Resources B1's
    locked Option A contract)."""
    if weekly_periods <= 0:
        raise InvalidTeachingAssignmentError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("NON_POSITIVE_WEEKLY_PERIODS", f"weekly_periods={weekly_periods} is not > 0"),),
        )
    if not any(t.id == teacher_id for t in problem.teachers):
        raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "teacher", teacher_id)
    require_ordinary_activity(problem, school_natural_id, academic_year_natural_id, activity_id)
    group = find_participant_group(problem, participant_group_id)
    if group is None:
        raise UnknownReferenceError(
            school_natural_id, academic_year_natural_id, "participant_group", participant_group_id,
        )
    if group.role != ParticipantGroupRole.WHOLE_CLASS:
        raise NonWholeClassTargetError(
            school_natural_id, academic_year_natural_id, participant_group_id, group.role.value,
        )
    duplicate = find_duplicate(problem, teacher_id, participant_group_id, activity_id)
    if duplicate is not None:
        raise DuplicateTeachingAssignmentError(
            school_natural_id, academic_year_natural_id, teacher_id, participant_group_id, activity_id,
        )
    if resource_id is not None:
        require_known_resource(problem, school_natural_id, academic_year_natural_id, resource_id)

    candidate_requirement = TeachingRequirement(
        id="__candidate__", teacher_id=teacher_id, activity_id=activity_id,
        participant_group_id=participant_group_id, weekly_periods=weekly_periods,
        resource_requirement=ResourceRequirement(resource_id=resource_id) if resource_id is not None else None,
    )
    candidate = replace(
        problem, teaching_requirements=problem.teaching_requirements + (candidate_requirement,),
    )
    new_errors = new_validation_errors(problem, candidate)
    blocking, warnings = split_blocking_and_warnings(new_errors)
    if blocking:
        raise InvalidTeachingAssignmentError(school_natural_id, academic_year_natural_id, blocking)
    return warnings


def validate_update(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    natural_id: str,
    *,
    teacher_id: str,
    participant_group_id: str,
    activity_id: str,
    weekly_periods: int,
    resource_id: str | None = None,
) -> tuple[ValidationError, ...]:
    """`resource_id` follows the same full-replacement contract as
    every other field here (Resources B1's locked Option A): `None`
    always clears any currently assigned Resource -- there is no way to
    say "leave the Resource unchanged" via this PUT."""
    existing = find_requirement(problem, natural_id)
    if existing is None:
        raise TeachingAssignmentNotFoundError(school_natural_id, academic_year_natural_id, natural_id)
    reasons = plain_reasons(problem, existing)
    if reasons:
        raise AdvancedRequirementNotEditableError(
            school_natural_id, academic_year_natural_id, natural_id, reasons,
        )
    if weekly_periods <= 0:
        raise InvalidTeachingAssignmentError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("NON_POSITIVE_WEEKLY_PERIODS", f"weekly_periods={weekly_periods} is not > 0"),),
        )
    if not any(t.id == teacher_id for t in problem.teachers):
        raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "teacher", teacher_id)
    require_ordinary_activity(problem, school_natural_id, academic_year_natural_id, activity_id)
    group = find_participant_group(problem, participant_group_id)
    if group is None:
        raise UnknownReferenceError(
            school_natural_id, academic_year_natural_id, "participant_group", participant_group_id,
        )
    if group.role != ParticipantGroupRole.WHOLE_CLASS:
        raise NonWholeClassTargetError(
            school_natural_id, academic_year_natural_id, participant_group_id, group.role.value,
        )
    duplicate = find_duplicate(
        problem, teacher_id, participant_group_id, activity_id, exclude_natural_id=natural_id,
    )
    if duplicate is not None:
        raise DuplicateTeachingAssignmentError(
            school_natural_id, academic_year_natural_id, teacher_id, participant_group_id, activity_id,
        )
    if resource_id is not None:
        require_known_resource(problem, school_natural_id, academic_year_natural_id, resource_id)

    updated_requirement = replace(
        existing, teacher_id=teacher_id, activity_id=activity_id,
        participant_group_id=participant_group_id, weekly_periods=weekly_periods,
        resource_requirement=ResourceRequirement(resource_id=resource_id) if resource_id is not None else None,
    )
    candidate = replace(
        problem,
        teaching_requirements=tuple(
            updated_requirement if r.id == natural_id else r for r in problem.teaching_requirements
        ),
    )
    new_errors = new_validation_errors(problem, candidate)
    blocking, warnings = split_blocking_and_warnings(new_errors)
    if blocking:
        raise InvalidTeachingAssignmentError(school_natural_id, academic_year_natural_id, blocking)
    return warnings


def validate_delete(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    natural_id: str,
) -> tuple[ValidationError, ...]:
    existing = find_requirement(problem, natural_id)
    if existing is None:
        raise TeachingAssignmentNotFoundError(school_natural_id, academic_year_natural_id, natural_id)
    reasons = plain_reasons(problem, existing)
    if reasons:
        raise AdvancedRequirementNotEditableError(
            school_natural_id, academic_year_natural_id, natural_id, reasons,
        )

    candidate = replace(
        problem,
        teaching_requirements=tuple(r for r in problem.teaching_requirements if r.id != natural_id),
    )
    new_errors = new_validation_errors(problem, candidate)
    blocking, warnings = split_blocking_and_warnings(new_errors)
    if blocking:
        raise InvalidTeachingAssignmentError(school_natural_id, academic_year_natural_id, blocking)
    return warnings
