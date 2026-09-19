"""Pure validation for the atomic synchronized two-branch split write."""
from __future__ import annotations

from dataclasses import replace

from school_timetable.application.errors import (
    ClassSectionNotFoundError,
    DuplicateSubgroupNameError,
    InvalidSynchronizedSplitError,
    PublicIdCollisionError,
    SameTeacherSynchronizedSplitError,
    UnknownReferenceError,
)
from school_timetable.application.synchronized_split_models import (
    CreateSynchronizedSplitCommand,
    SynchronizedSplitBranchFields,
    SynchronizedSplitPublicIds,
)
from school_timetable.application.teaching_assignment_rules import (
    new_validation_errors,
    require_ordinary_activity,
    split_blocking_and_warnings,
)
from school_timetable.domain.groups import ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import TeachingRequirement
from school_timetable.validation.errors import ValidationError


def normalize_command(command: CreateSynchronizedSplitCommand) -> CreateSynchronizedSplitCommand:
    return replace(
        command,
        branch_a=replace(command.branch_a, participant_group_name=command.branch_a.participant_group_name.strip()),
        branch_b=replace(command.branch_b, participant_group_name=command.branch_b.participant_group_name.strip()),
    )


def validate_create(
    problem: SchedulingProblem,
    command: CreateSynchronizedSplitCommand,
    public_ids: SynchronizedSplitPublicIds,
) -> tuple[ValidationError, ...]:
    if command.weekly_periods <= 0:
        raise InvalidSynchronizedSplitError((ValidationError(
            "NON_POSITIVE_WEEKLY_PERIODS", f"weekly_periods={command.weekly_periods} is not > 0",
        ),))
    if command.branch_a.participant_group_name == "" or command.branch_b.participant_group_name == "":
        raise InvalidSynchronizedSplitError((ValidationError(
            "BLANK_SUBGROUP_NAME", "participant_group_name is blank after trimming",
        ),))
    if command.branch_a.participant_group_name == command.branch_b.participant_group_name:
        raise DuplicateSubgroupNameError(command.branch_a.participant_group_name)
    if command.branch_a.teacher_id == command.branch_b.teacher_id:
        raise SameTeacherSynchronizedSplitError(command.branch_a.teacher_id)
    if not any(c.id == command.class_section_id for c in problem.class_sections):
        raise ClassSectionNotFoundError(command.school_id, command.academic_year_id, command.class_section_id)

    for branch in (command.branch_a, command.branch_b):
        _validate_branch_reference(problem, command, branch)

    generated = (
        public_ids.split_group_id,
        public_ids.branch_a_group_id,
        public_ids.branch_b_group_id,
        public_ids.branch_a_requirement_id,
        public_ids.branch_b_requirement_id,
    )
    if len(set(generated)) != len(generated):
        raise PublicIdCollisionError(next(value for value in generated if generated.count(value) > 1))
    existing_ids = (
        {g.id for g in problem.participant_groups}
        | {r.id for r in problem.teaching_requirements}
        | {r.split_group_id for r in problem.teaching_requirements if r.split_group_id is not None}
    )
    collision = next((value for value in generated if value in existing_ids), None)
    if collision is not None:
        raise PublicIdCollisionError(collision)

    groups = (
        ParticipantGroup(public_ids.branch_a_group_id, command.branch_a.participant_group_name,
                         (command.class_section_id,), ParticipantGroupRole.SUBGROUP),
        ParticipantGroup(public_ids.branch_b_group_id, command.branch_b.participant_group_name,
                         (command.class_section_id,), ParticipantGroupRole.SUBGROUP),
    )
    requirements = (
        _requirement(command.branch_a, public_ids.branch_a_requirement_id,
                     public_ids.branch_a_group_id, public_ids.split_group_id, command.weekly_periods),
        _requirement(command.branch_b, public_ids.branch_b_requirement_id,
                     public_ids.branch_b_group_id, public_ids.split_group_id, command.weekly_periods),
    )
    candidate = replace(
        problem,
        participant_groups=problem.participant_groups + groups,
        teaching_requirements=problem.teaching_requirements + requirements,
    )
    blocking, warnings = split_blocking_and_warnings(new_validation_errors(problem, candidate))
    if blocking:
        raise InvalidSynchronizedSplitError(blocking)
    return warnings


def _validate_branch_reference(problem, command, branch: SynchronizedSplitBranchFields) -> None:
    if not any(t.id == branch.teacher_id for t in problem.teachers):
        raise UnknownReferenceError(command.school_id, command.academic_year_id, "teacher", branch.teacher_id)
    require_ordinary_activity(problem, command.school_id, command.academic_year_id, branch.activity_id)


def _requirement(branch, requirement_id, group_id, split_group_id, weekly_periods) -> TeachingRequirement:
    return TeachingRequirement(
        id=requirement_id, teacher_id=branch.teacher_id, activity_id=branch.activity_id,
        participant_group_id=group_id, weekly_periods=weekly_periods, split_group_id=split_group_id,
    )
