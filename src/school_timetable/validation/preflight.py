"""Preflight validation: catch obviously invalid input before CP-SAT ever
runs. This module never builds or inspects a CP-SAT model -- it only
reasons about the plain domain objects.
"""
from __future__ import annotations

from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.people import AvailabilityStatus
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode
from school_timetable.validation.errors import ValidationError


def run_preflight(problem: SchedulingProblem) -> list[ValidationError]:
    index = ProblemIndex(problem)
    errors: list[ValidationError] = []

    errors.extend(_check_references(problem, index))
    # Further checks assume references resolve, so stop early if they don't
    # to avoid noisy KeyErrors cascading into unrelated messages.
    if errors:
        return errors

    errors.extend(_check_block_patterns(problem))
    errors.extend(_check_split_group_consistency(problem, index))
    errors.extend(_check_fixed_placement_availability(problem, index))
    errors.extend(_check_teacher_load_vs_availability(problem, index))
    errors.extend(_check_class_full_occupancy(problem, index))

    return errors


def _check_references(problem: SchedulingProblem, index: ProblemIndex) -> list[ValidationError]:
    errors: list[ValidationError] = []

    for req in problem.teaching_requirements:
        if req.teacher_id not in index.teachers_by_id:
            errors.append(ValidationError(
                "UNKNOWN_TEACHER", f"Requirement {req.id!r} references unknown teacher {req.teacher_id!r}",
                {"requirement_id": req.id, "teacher_id": req.teacher_id},
            ))
        if req.activity_id not in index.activities_by_id:
            errors.append(ValidationError(
                "UNKNOWN_ACTIVITY", f"Requirement {req.id!r} references unknown activity {req.activity_id!r}",
                {"requirement_id": req.id, "activity_id": req.activity_id},
            ))
        if req.participant_group_id not in index.participant_groups_by_id:
            errors.append(ValidationError(
                "UNKNOWN_PARTICIPANT_GROUP",
                f"Requirement {req.id!r} references unknown participant group {req.participant_group_id!r}",
                {"requirement_id": req.id, "participant_group_id": req.participant_group_id},
            ))
        else:
            group = index.participant_groups_by_id[req.participant_group_id]
            for class_id in group.class_sections:
                if class_id not in index.class_sections_by_id:
                    errors.append(ValidationError(
                        "UNKNOWN_CLASS_SECTION",
                        f"Participant group {group.id!r} references unknown class {class_id!r}",
                        {"participant_group_id": group.id, "class_id": class_id},
                    ))
        if req.resource_requirement is not None:
            if req.resource_requirement.resource_id not in index.resources_by_id:
                errors.append(ValidationError(
                    "UNKNOWN_RESOURCE",
                    f"Requirement {req.id!r} references unknown resource "
                    f"{req.resource_requirement.resource_id!r}",
                    {"requirement_id": req.id, "resource_id": req.resource_requirement.resource_id},
                ))
        if req.weekly_periods <= 0:
            errors.append(ValidationError(
                "NON_POSITIVE_WEEKLY_PERIODS",
                f"Requirement {req.id!r} has non-positive weekly_periods={req.weekly_periods}",
                {"requirement_id": req.id},
            ))

    for avail in problem.teacher_availabilities:
        if avail.teacher_id not in index.teachers_by_id:
            errors.append(ValidationError(
                "UNKNOWN_TEACHER",
                f"Teacher availability references unknown teacher {avail.teacher_id!r}",
                {"teacher_id": avail.teacher_id},
            ))
        if avail.day_id not in index.days_by_id or avail.period_id not in index.periods_by_id:
            errors.append(ValidationError(
                "UNKNOWN_SLOT",
                f"Teacher availability for {avail.teacher_id!r} references unknown slot "
                f"({avail.day_id!r}, {avail.period_id!r})",
                {"teacher_id": avail.teacher_id, "day_id": avail.day_id, "period_id": avail.period_id},
            ))

    for block in problem.reserved_blocks:
        if block.activity_id not in index.activities_by_id:
            errors.append(ValidationError(
                "UNKNOWN_ACTIVITY",
                f"Reserved block {block.id!r} references unknown activity {block.activity_id!r}",
                {"reserved_block_id": block.id, "activity_id": block.activity_id},
            ))
        if block.teacher_id is not None and block.teacher_id not in index.teachers_by_id:
            errors.append(ValidationError(
                "UNKNOWN_TEACHER",
                f"Reserved block {block.id!r} references unknown teacher {block.teacher_id!r}",
                {"reserved_block_id": block.id, "teacher_id": block.teacher_id},
            ))
        for class_id in block.class_sections:
            if class_id not in index.class_sections_by_id:
                errors.append(ValidationError(
                    "UNKNOWN_CLASS_SECTION",
                    f"Reserved block {block.id!r} references unknown class {class_id!r}",
                    {"reserved_block_id": block.id, "class_id": class_id},
                ))
        for slot in block.slots:
            if slot.day_id not in index.days_by_id or slot.period_id not in index.periods_by_id:
                errors.append(ValidationError(
                    "UNKNOWN_SLOT",
                    f"Reserved block {block.id!r} references unknown slot "
                    f"({slot.day_id!r}, {slot.period_id!r})",
                    {"reserved_block_id": block.id},
                ))

    for fp in problem.fixed_placements:
        if fp.requirement_id not in index.requirements_by_id:
            errors.append(ValidationError(
                "UNKNOWN_REQUIREMENT",
                f"Fixed placement {fp.id!r} references unknown requirement {fp.requirement_id!r}",
                {"fixed_placement_id": fp.id, "requirement_id": fp.requirement_id},
            ))
        if fp.slot.day_id not in index.days_by_id or fp.slot.period_id not in index.periods_by_id:
            errors.append(ValidationError(
                "UNKNOWN_SLOT",
                f"Fixed placement {fp.id!r} references unknown slot "
                f"({fp.slot.day_id!r}, {fp.slot.period_id!r})",
                {"fixed_placement_id": fp.id},
            ))

    return errors


def _check_block_patterns(problem: SchedulingProblem) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for req in problem.teaching_requirements:
        policy = req.block_policy
        if policy.mode == BlockPolicyMode.FLEXIBLE:
            continue
        sizes = policy.block_sizes
        if not sizes:
            errors.append(ValidationError(
                "INVALID_BLOCK_PATTERN",
                f"Requirement {req.id!r} has mode {policy.mode.value} but no block_sizes",
                {"requirement_id": req.id},
            ))
            continue
        if any(size not in (1, 2) for size in sizes):
            errors.append(ValidationError(
                "UNSUPPORTED_BLOCK_SIZE",
                f"Requirement {req.id!r} block_sizes {sizes!r} contains a size other than 1 or 2; "
                "this PoC solver only supports single lessons and one double lesson per requirement",
                {"requirement_id": req.id},
            ))
            continue
        if sizes.count(2) > 1:
            errors.append(ValidationError(
                "UNSUPPORTED_BLOCK_SIZE",
                f"Requirement {req.id!r} block_sizes {sizes!r} requests more than one double lesson; "
                "this PoC solver supports at most one double lesson per requirement",
                {"requirement_id": req.id},
            ))
            continue
        if sum(sizes) != req.weekly_periods:
            errors.append(ValidationError(
                "BLOCK_PATTERN_TOTAL_MISMATCH",
                f"Requirement {req.id!r} block_sizes {sizes!r} sum to {sum(sizes)}, "
                f"but weekly_periods is {req.weekly_periods}",
                {"requirement_id": req.id},
            ))
    return errors


def _check_split_group_consistency(problem: SchedulingProblem, index: ProblemIndex) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for split_id, req_ids in index.split_groups.items():
        if len(req_ids) < 2:
            errors.append(ValidationError(
                "DEGENERATE_SPLIT_GROUP",
                f"Split group {split_id!r} has fewer than two branches: {req_ids!r}",
                {"split_group_id": split_id},
            ))
            continue
        reqs = [index.requirements_by_id[rid] for rid in req_ids]
        weekly_periods = {r.weekly_periods for r in reqs}
        if len(weekly_periods) > 1:
            errors.append(ValidationError(
                "SPLIT_GROUP_WEEKLY_PERIODS_MISMATCH",
                f"Split group {split_id!r} branches have differing weekly_periods: "
                f"{ {r.id: r.weekly_periods for r in reqs} }",
                {"split_group_id": split_id},
            ))
        class_sets = {
            tuple(sorted(index.participant_groups_by_id[r.participant_group_id].class_sections))
            for r in reqs
        }
        if len(class_sets) > 1:
            errors.append(ValidationError(
                "SPLIT_GROUP_CLASS_MISMATCH",
                f"Split group {split_id!r} branches occupy different classes: {class_sets!r}",
                {"split_group_id": split_id},
            ))
    return errors


def _check_fixed_placement_availability(problem: SchedulingProblem, index: ProblemIndex) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for fp in problem.fixed_placements:
        req = index.requirements_by_id.get(fp.requirement_id)
        if req is None:
            continue  # already reported by _check_references
        status = index.get_availability(req.teacher_id, fp.slot.day_id, fp.slot.period_id)
        if status == AvailabilityStatus.UNAVAILABLE:
            errors.append(ValidationError(
                "FIXED_PLACEMENT_TEACHER_UNAVAILABLE",
                f"Fixed placement {fp.id!r} pins requirement {req.id!r} (teacher {req.teacher_id!r}) "
                f"to slot ({fp.slot.day_id!r}, {fp.slot.period_id!r}) where the teacher is UNAVAILABLE",
                {"fixed_placement_id": fp.id, "requirement_id": req.id},
            ))
    return errors


def _check_teacher_load_vs_availability(problem: SchedulingProblem, index: ProblemIndex) -> list[ValidationError]:
    errors: list[ValidationError] = []
    load_by_teacher: dict[str, int] = {}
    for req in problem.teaching_requirements:
        load_by_teacher[req.teacher_id] = load_by_teacher.get(req.teacher_id, 0) + req.weekly_periods

    for teacher_id, load in load_by_teacher.items():
        available = 0
        for slot in index.all_slots:
            if index.is_reserved_for_teacher(teacher_id, slot.day_id, slot.period_id):
                continue
            if index.get_availability(teacher_id, slot.day_id, slot.period_id) == AvailabilityStatus.UNAVAILABLE:
                continue
            available += 1
        if load > available:
            errors.append(ValidationError(
                "TEACHER_OVERLOADED",
                f"Teacher {teacher_id!r} has {load} required weekly periods but only "
                f"{available} usable slots",
                {"teacher_id": teacher_id, "required": load, "available": available},
            ))
    return errors


def _check_class_full_occupancy(problem: SchedulingProblem, index: ProblemIndex) -> list[ValidationError]:
    """The pilot fixture requires every main class to be occupied in every
    instructional slot. Check that each class's declared workload sums to
    exactly the number of instructional slots per week, before the solver
    ever has a chance to fail to find such a schedule."""
    errors: list[ValidationError] = []
    total_slots = len(index.all_slots)

    load_by_class: dict[str, int] = {c.id: 0 for c in problem.class_sections}
    for req in problem.teaching_requirements:
        if not index.is_split_branch_representative(req):
            continue
        group = index.participant_groups_by_id[req.participant_group_id]
        for class_id in group.class_sections:
            if class_id in load_by_class:
                load_by_class[class_id] += req.weekly_periods

    reserved_by_class: dict[str, int] = {c.id: 0 for c in problem.class_sections}
    for block in problem.reserved_blocks:
        for class_id in block.class_sections:
            if class_id in reserved_by_class:
                reserved_by_class[class_id] += len(block.slots)

    for class_id in load_by_class:
        total = load_by_class[class_id] + reserved_by_class[class_id]
        if total != total_slots:
            errors.append(ValidationError(
                "CLASS_OCCUPANCY_MISMATCH",
                f"Class {class_id!r} has {total} declared periods (lessons + reserved blocks) "
                f"but there are {total_slots} instructional slots per week",
                {"class_id": class_id, "declared": total, "required": total_slots},
            ))
    return errors
