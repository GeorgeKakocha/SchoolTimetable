"""Preflight validation: catch obviously invalid input before CP-SAT ever
runs. This module never builds or inspects a CP-SAT model -- it only
reasons about the plain domain objects.
"""
from __future__ import annotations

from school_timetable.domain.activities import ActivityKind
from school_timetable.domain.calendar import period_windows
from school_timetable.domain.groups import ParticipantGroupRole
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.people import AvailabilityStatus
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, TeachingRequirement
from school_timetable.validation.errors import ValidationError


def run_preflight(problem: SchedulingProblem) -> list[ValidationError]:
    index = ProblemIndex(problem)
    errors: list[ValidationError] = []

    errors.extend(_check_references(problem, index))
    # Further checks assume references resolve, so stop early if they don't
    # to avoid noisy KeyErrors cascading into unrelated messages.
    if errors:
        return errors

    errors.extend(_check_participant_group_roles(problem))
    errors.extend(_check_teaching_requirement_activity_kind(problem, index))
    errors.extend(_check_reserved_block_duplicates(problem))
    errors.extend(_check_reserved_block_activity_kind(problem, index))
    errors.extend(_check_reserved_block_instructional_slots(problem, index))
    errors.extend(_check_reserved_block_teacher_availability(problem, index))
    errors.extend(_check_reserved_block_collisions(problem, index))
    errors.extend(_check_block_patterns(problem, index))
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

    errors.extend(_check_duplicate_teacher_availability_cells(problem))

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


def _check_duplicate_teacher_availability_cells(problem: SchedulingProblem) -> list[ValidationError]:
    """Defense-in-depth (Owner Decision #38): an in-memory
    `SchedulingProblem` could in principle contain two
    `TeacherAvailability` entries for the identical `(teacher_id,
    day_id, period_id)` cell -- persistence itself cannot produce this
    (the table's composite primary key forbids it), but nothing
    upstream of preflight guarantees it for an arbitrary in-memory/
    imported problem, and `ProblemIndex.get_availability` would
    otherwise silently let the last one win. Reported once per
    duplicated cell, regardless of how many times it repeats or
    whether the repeated entries agree on `status`."""
    errors: list[ValidationError] = []
    seen: set[tuple[str, str, str]] = set()
    duplicates: set[tuple[str, str, str]] = set()
    for avail in problem.teacher_availabilities:
        cell = (avail.teacher_id, avail.day_id, avail.period_id)
        if cell in seen:
            duplicates.add(cell)
        seen.add(cell)
    for teacher_id, day_id, period_id in sorted(duplicates):
        errors.append(ValidationError(
            "DUPLICATE_TEACHER_AVAILABILITY_CELL",
            f"Teacher {teacher_id!r} has more than one availability entry for slot "
            f"({day_id!r}, {period_id!r})",
            {"teacher_id": teacher_id, "day_id": day_id, "period_id": period_id},
        ))
    return errors


def _check_participant_group_roles(problem: SchedulingProblem) -> list[ValidationError]:
    """`ParticipantGroup.role` structural invariants (`DECISIONS.md` #33).
    `role` itself is never inferred here or anywhere else -- this only
    checks that an already-assigned role is structurally consistent.
    Cardinality is always evaluated against the number of *distinct*
    ClassSections, never raw tuple length: a directly-constructed
    `SchedulingProblem` (preflight is callable independently of
    persistence, where a `UNIQUE` constraint already rules this out) can
    still contain a duplicated `class_sections` entry, e.g.
    `("c1", "c1")` -- that must never let MERGED_CLASSES appear
    structurally valid merely because the tuple happens to have length
    2. WHOLE_CLASS/SUBGROUP must draw from exactly one distinct
    ClassSection, MERGED_CLASSES from two or more distinct
    ClassSections, and every ClassSection must have exactly one
    canonical WHOLE_CLASS group. Assumes `_check_references` has
    already run with no errors, so every `class_sections` entry is a
    known-good ClassSection ID."""
    errors: list[ValidationError] = []

    whole_class_owners: dict[str, list[str]] = {c.id: [] for c in problem.class_sections}

    for group in problem.participant_groups:
        distinct_class_sections = set(group.class_sections)
        if len(distinct_class_sections) != len(group.class_sections):
            errors.append(ValidationError(
                "PARTICIPANT_GROUP_DUPLICATE_CLASS_SECTION",
                f"Participant group {group.id!r} lists the same ClassSection more than "
                f"once in class_sections {group.class_sections!r}",
                {"participant_group_id": group.id},
            ))

        n = len(distinct_class_sections)
        if group.role in (ParticipantGroupRole.WHOLE_CLASS, ParticipantGroupRole.SUBGROUP):
            if n != 1:
                errors.append(ValidationError(
                    "PARTICIPANT_GROUP_ROLE_CARDINALITY_MISMATCH",
                    f"Participant group {group.id!r} has role {group.role.value} but "
                    f"{n} distinct class_sections {group.class_sections!r} (expected exactly 1)",
                    {"participant_group_id": group.id, "role": group.role.value},
                ))
        elif group.role == ParticipantGroupRole.MERGED_CLASSES and n < 2:
            errors.append(ValidationError(
                "PARTICIPANT_GROUP_ROLE_CARDINALITY_MISMATCH",
                f"Participant group {group.id!r} has role MERGED_CLASSES but "
                f"{n} distinct class_sections {group.class_sections!r} (expected 2 or more)",
                {"participant_group_id": group.id, "role": group.role.value},
            ))

        if group.role == ParticipantGroupRole.WHOLE_CLASS and n == 1:
            whole_class_owners.setdefault(group.class_sections[0], []).append(group.id)

    for class_id, owners in whole_class_owners.items():
        if len(owners) != 1:
            errors.append(ValidationError(
                "CLASS_SECTION_WHOLE_CLASS_GROUP_COUNT_MISMATCH",
                f"ClassSection {class_id!r} has {len(owners)} WHOLE_CLASS participant "
                f"groups (expected exactly 1): {owners!r}",
                {"class_id": class_id, "whole_class_group_ids": owners},
            ))

    return errors


def _check_teaching_requirement_activity_kind(
    problem: SchedulingProblem, index: ProblemIndex,
) -> list[ValidationError]:
    """Defense-in-depth (pre-Slice-D correction): every
    `TeachingRequirement.activity_id` must reference an
    `ActivityKind.ORDINARY` activity -- `CLUB` activities are scheduled
    via `ReservedBlock`, never a `TeachingRequirement` (see
    `domain/activities.py`). `TeachingAssignmentService`'s own write
    validation (`teaching_assignment_rules.require_ordinary_activity`)
    already rejects this at save time; this check exists only to catch
    an already-malformed `SchedulingProblem` reaching preflight by some
    other path (legacy data, direct construction, future import/admin
    tooling) before it can ever reach the solver. Assumes
    `_check_references` has already run with no errors, so every
    `activity_id` is a known-good key in `index.activities_by_id`."""
    errors: list[ValidationError] = []

    for req in problem.teaching_requirements:
        activity = index.activities_by_id[req.activity_id]
        if activity.kind != ActivityKind.ORDINARY:
            errors.append(ValidationError(
                "NON_ORDINARY_TEACHING_REQUIREMENT_ACTIVITY",
                f"Requirement {req.id!r} references activity {req.activity_id!r} with kind "
                f"{activity.kind.value!r}, expected ORDINARY",
                {"requirement_id": req.id, "activity_id": req.activity_id, "actual_kind": activity.kind.value},
            ))

    return errors


def _check_reserved_block_duplicates(problem: SchedulingProblem) -> list[ValidationError]:
    """Defense-in-depth (Reserved Activities Slice A2), mirroring
    `_check_duplicate_teacher_availability_cells` exactly: an in-memory
    `ReservedBlock` could in principle repeat the identical
    `ClassSection` or the identical `(day_id, period_id)` slot within
    its own `class_sections`/`slots` tuples -- persistence itself
    cannot produce this (`uq_rbcs_block_class`/`uq_rbs_block_day_period`
    forbid it), but nothing upstream of preflight guarantees it for an
    arbitrary in-memory/imported problem. These are purely
    within-one-block structural checks -- never confused with a
    cross-block collision (`_check_reserved_block_collisions`)."""
    errors: list[ValidationError] = []
    for block in problem.reserved_blocks:
        seen_classes: set[str] = set()
        dup_classes: set[str] = set()
        for class_id in block.class_sections:
            if class_id in seen_classes:
                dup_classes.add(class_id)
            seen_classes.add(class_id)
        for class_id in sorted(dup_classes):
            errors.append(ValidationError(
                "DUPLICATE_RESERVED_BLOCK_CLASS_SECTION",
                f"Reserved block {block.id!r} references class {class_id!r} more than once",
                {"reserved_block_id": block.id, "class_id": class_id},
            ))

        seen_slots: set[tuple[str, str]] = set()
        dup_slots: set[tuple[str, str]] = set()
        for slot in block.slots:
            key = (slot.day_id, slot.period_id)
            if key in seen_slots:
                dup_slots.add(key)
            seen_slots.add(key)
        for day_id, period_id in sorted(dup_slots):
            errors.append(ValidationError(
                "DUPLICATE_RESERVED_BLOCK_SLOT",
                f"Reserved block {block.id!r} has more than one entry for slot "
                f"({day_id!r}, {period_id!r})",
                {"reserved_block_id": block.id, "day_id": day_id, "period_id": period_id},
            ))
    return errors


def _check_reserved_block_activity_kind(problem: SchedulingProblem, index: ProblemIndex) -> list[ValidationError]:
    """Reserved Activities Slice A2 -- the symmetric mirror of
    `_check_teaching_requirement_activity_kind`: every
    `ReservedBlock.activity_id` must reference an `ActivityKind.CLUB`
    activity -- `ORDINARY` activities are scheduled via a
    `TeachingRequirement`, never a `ReservedBlock` (see
    `domain/activities.py`). The application write service's own
    `NonSpecialActivityTargetError` already rejects this at save time;
    this check exists only to catch an already-malformed
    `SchedulingProblem` reaching preflight by some other path (legacy
    data, direct construction, future import/admin tooling). Assumes
    `_check_references` has already run with no errors, so every
    `activity_id` is a known-good key in `index.activities_by_id` --
    `UNKNOWN_ACTIVITY` and this check therefore never both fire for the
    same reference. The diagnostic message deliberately avoids raw
    enum vocabulary; the raw `kind` value is still recorded in
    `context` for the independent verifier's own internal use, never
    surfaced through the dedicated Reserved Activity write API."""
    errors: list[ValidationError] = []
    for block in problem.reserved_blocks:
        activity = index.activities_by_id[block.activity_id]
        if activity.kind != ActivityKind.CLUB:
            errors.append(ValidationError(
                "RESERVED_BLOCK_NON_CLUB_ACTIVITY",
                f"Reserved block {block.id!r} references an activity that is not valid as a "
                "Special Activity",
                {"reserved_block_id": block.id, "activity_id": block.activity_id, "actual_kind": activity.kind.value},
            ))
    return errors


def _check_reserved_block_instructional_slots(
    problem: SchedulingProblem, index: ProblemIndex,
) -> list[ValidationError]:
    """Reserved Activities Slice A2: every `ReservedBlock` slot must be
    an instructional `Period` -- the solver never places an ordinary
    lesson on a non-instructional period regardless of any reservation
    (`domain/indexing.py`'s `instructional_periods_sorted` is the only
    period set the model builder ever iterates), so a reservation there
    would be silently invisible in both Class and Teacher Timetable
    projections (both filter to `is_instructional` periods) even though
    it round-trips through persistence. Assumes `_check_references` has
    already run with no errors, so every slot's `day_id`/`period_id`
    is already known-good -- `UNKNOWN_SLOT` and this check never both
    fire for the same slot."""
    errors: list[ValidationError] = []
    for block in problem.reserved_blocks:
        for slot in block.slots:
            period = index.periods_by_id[slot.period_id]
            if not period.is_instructional:
                errors.append(ValidationError(
                    "RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT",
                    f"Reserved block {block.id!r} references non-instructional period "
                    f"{slot.period_id!r} on day {slot.day_id!r}",
                    {"reserved_block_id": block.id, "day_id": slot.day_id, "period_id": slot.period_id},
                ))
    return errors


def _check_reserved_block_teacher_availability(
    problem: SchedulingProblem, index: ProblemIndex,
) -> list[ValidationError]:
    """Reserved Activities Slice A2: a teacher-attached `ReservedBlock`
    is an administrative fixed commitment with no solver choice to
    optimize, so `PREFER_NOT` never blocks it and never adds a soft
    penalty -- but `UNAVAILABLE` is a contradictory hard configuration
    (the school is fixing this Teacher into a slot the Teacher has
    explicitly marked as genuinely unavailable) and must be rejected.
    Only evaluated for blocks with a non-null `teacher_id`; assumes
    `_check_references` has already run with no errors, so
    `block.teacher_id` (when set) is already known-good --
    `UNKNOWN_TEACHER` and this check never both fire for the same
    reference."""
    errors: list[ValidationError] = []
    for block in problem.reserved_blocks:
        if block.teacher_id is None:
            continue
        for slot in block.slots:
            status = index.get_availability(block.teacher_id, slot.day_id, slot.period_id)
            if status == AvailabilityStatus.UNAVAILABLE:
                errors.append(ValidationError(
                    "RESERVED_BLOCK_TEACHER_UNAVAILABLE",
                    f"Reserved block {block.id!r} assigns teacher {block.teacher_id!r} to slot "
                    f"({slot.day_id!r}, {slot.period_id!r}) where the teacher is UNAVAILABLE",
                    {
                        "reserved_block_id": block.id, "teacher_id": block.teacher_id,
                        "day_id": slot.day_id, "period_id": slot.period_id,
                    },
                ))
    return errors


def _check_reserved_block_collisions(problem: SchedulingProblem, index: ProblemIndex) -> list[ValidationError]:
    """Reserved Activities Slice A2: two DIFFERENT `ReservedBlock`s may
    never claim the same `(ClassSection, Day, Period)`, nor may two
    DIFFERENT teacher-attached `ReservedBlock`s claim the same
    `(Teacher, Day, Period)` -- deliberately NOT detected via
    `ProblemIndex.reserved_class_slots`/`reserved_teacher_slots`, which
    are plain last-writer-wins dicts built for the solver's own
    exclusion lookups, never a validator. A direct deterministic scan
    instead: traverses `problem.reserved_blocks` in their own
    (already-ordinal-ordered) list order; within each block, its own
    `class_sections`/`slots` are re-sorted here by the authoritative
    `ClassSection` problem order and `Day.index`/`Period.index` (never
    trusted to already be canonical -- preflight is callable against
    any in-memory/imported problem, not only ones this phase's own
    write service produced), remembers the first block to claim each
    key, and emits one directional diagnostic naming the later
    conflicting block and the earlier first-owner block for every
    subsequent claim -- never a mirrored pair. Two entries sharing one
    `id` never occur here in practice: the write service always
    replaces (never duplicates) a target block when constructing an
    update candidate, so a block can never collide with itself through
    the normal write path; the `owner != block.id` guard is still kept
    as an explicit, cheap defensive check."""
    errors: list[ValidationError] = []
    class_position = {c.id: i for i, c in enumerate(problem.class_sections)}

    def slot_sort_key(slot):
        return (index.days_by_id[slot.day_id].index, index.periods_by_id[slot.period_id].index)

    class_owner: dict[tuple[str, str, str], str] = {}
    for block in problem.reserved_blocks:
        ordered_classes = sorted(block.class_sections, key=lambda c: class_position.get(c, len(class_position)))
        ordered_slots = sorted(block.slots, key=slot_sort_key)
        for class_id in ordered_classes:
            for slot in ordered_slots:
                key = (class_id, slot.day_id, slot.period_id)
                owner = class_owner.get(key)
                if owner is None:
                    class_owner[key] = block.id
                elif owner != block.id:
                    errors.append(ValidationError(
                        "RESERVED_BLOCK_CLASS_SLOT_COLLISION",
                        f"Reserved block {block.id!r} conflicts with reserved block {owner!r}: both "
                        f"claim class {class_id!r} at slot ({slot.day_id!r}, {slot.period_id!r})",
                        {
                            "reserved_block_id": block.id, "conflicting_reserved_block_id": owner,
                            "class_section_id": class_id, "day_id": slot.day_id, "period_id": slot.period_id,
                        },
                    ))

    teacher_owner: dict[tuple[str, str, str], str] = {}
    for block in problem.reserved_blocks:
        if block.teacher_id is None:
            continue
        ordered_slots = sorted(block.slots, key=slot_sort_key)
        for slot in ordered_slots:
            key = (block.teacher_id, slot.day_id, slot.period_id)
            owner = teacher_owner.get(key)
            if owner is None:
                teacher_owner[key] = block.id
            elif owner != block.id:
                errors.append(ValidationError(
                    "RESERVED_BLOCK_TEACHER_SLOT_COLLISION",
                    f"Reserved block {block.id!r} conflicts with reserved block {owner!r}: both "
                    f"assign teacher {block.teacher_id!r} at slot ({slot.day_id!r}, {slot.period_id!r})",
                    {
                        "reserved_block_id": block.id, "conflicting_reserved_block_id": owner,
                        "teacher_id": block.teacher_id, "day_id": slot.day_id, "period_id": slot.period_id,
                    },
                ))

    return errors


def _check_block_patterns(problem: SchedulingProblem, index: ProblemIndex) -> list[ValidationError]:
    """Validate ``LessonBlockPolicy.block_sizes`` shape.

    REQUIRED patterns are generic (Phase 2A): any multiset of positive
    integers summing to ``weekly_periods``, checked for placeability
    against the actual calendar shape. PREFERRED intentionally keeps the
    original Phase-1 restriction (at most one size-2 block, rest singles)
    -- see ``LessonBlockPolicy`` for why this was not generalized here.
    """
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
        if any(not isinstance(size, int) or size <= 0 for size in sizes):
            errors.append(ValidationError(
                "NON_POSITIVE_BLOCK_LENGTH",
                f"Requirement {req.id!r} block_sizes {sizes!r} contains a non-positive or "
                "non-integer block length",
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
            continue

        if policy.mode == BlockPolicyMode.PREFERRED:
            errors.extend(_check_preferred_block_pattern(req, sizes))
            continue

        errors.extend(_check_required_block_pattern(req, sizes, index))
    return errors


def _check_preferred_block_pattern(
    req: TeachingRequirement, sizes: tuple[int, ...]
) -> list[ValidationError]:
    """Phase-1-shaped PREFERRED restriction, unchanged: at most one
    size-2 block, the rest singles. Intentionally not generalized -- see
    ``LessonBlockPolicy``."""
    if any(size not in (1, 2) for size in sizes):
        return [ValidationError(
            "UNSUPPORTED_BLOCK_SIZE",
            f"Requirement {req.id!r} block_sizes {sizes!r} contains a size other than 1 or 2; "
            "PREFERRED patterns only support single lessons and one double lesson per requirement",
            {"requirement_id": req.id},
        )]
    if sizes.count(2) > 1:
        return [ValidationError(
            "UNSUPPORTED_BLOCK_SIZE",
            f"Requirement {req.id!r} block_sizes {sizes!r} requests more than one double lesson; "
            "PREFERRED patterns support at most one double lesson per requirement",
            {"requirement_id": req.id},
        )]
    return []


def _check_required_block_pattern(
    req: TeachingRequirement, sizes: tuple[int, ...], index: ProblemIndex
) -> list[ValidationError]:
    """Generic REQUIRED pattern placeability checks (Phase 2A, rules 7-9):
    enough days to host every block on its own day, no block longer than
    an allowed max_periods_per_day, and every block length actually fits
    inside some consecutive same-block_id run of periods."""
    errors: list[ValidationError] = []
    num_days = len(index.days_sorted)

    if len(sizes) > num_days:
        errors.append(ValidationError(
            "TOO_MANY_BLOCKS_FOR_AVAILABLE_DAYS",
            f"Requirement {req.id!r} block_sizes {sizes!r} needs {len(sizes)} distinct days, "
            f"but only {num_days} school days are configured",
            {"requirement_id": req.id, "blocks": len(sizes), "available_days": num_days},
        ))

    max_block = max(sizes)
    max_per_day = req.distribution_policy.max_periods_per_day
    if max_per_day is not None and max_per_day < max_block:
        errors.append(ValidationError(
            "BLOCK_EXCEEDS_MAX_PERIODS_PER_DAY",
            f"Requirement {req.id!r} has a block of length {max_block}, but "
            f"max_periods_per_day is {max_per_day}",
            {"requirement_id": req.id, "max_block": max_block, "max_periods_per_day": max_per_day},
        ))

    for length in sorted(set(sizes)):
        if length == 1:
            continue
        if not period_windows(index.instructional_periods_sorted, length):
            errors.append(ValidationError(
                "BLOCK_LENGTH_UNPLACEABLE",
                f"Requirement {req.id!r} needs a block of length {length}, but no configured "
                "run of consecutive same-block_id periods is long enough to hold it",
                {"requirement_id": req.id, "block_length": length},
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
