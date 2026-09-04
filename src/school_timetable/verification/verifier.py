"""Independent verification of a candidate schedule.

This module does NOT read or trust any CP-SAT internals -- it only looks at
the final ``ScheduleEntry`` list and the plain domain model, and
independently re-derives whether every hard constraint actually holds. If
the solver claims FEASIBLE/OPTIMAL but this module disagrees, that is a
solver-modeling bug, never a reason to relax this module's checks.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.people import AvailabilityStatus
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode
from school_timetable.domain.result import EntrySource, ScheduleEntry


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    violations: tuple[str, ...] = ()


def verify(problem: SchedulingProblem, entries: tuple[ScheduleEntry, ...]) -> VerificationReport:
    index = ProblemIndex(problem)
    violations: list[str] = []

    violations += _check_teacher_non_overlap(entries)
    violations += _check_participant_group_non_overlap(entries)
    violations += _check_weekly_lesson_counts(problem, entries)
    violations += _check_teacher_unavailable_respected(index, entries)
    violations += _check_fixed_placements_respected(problem, entries)
    violations += _check_required_block_patterns(problem, index, entries)
    violations += _check_reserved_blocks_respected(problem, entries)
    violations += _check_split_groups_synchronized(index, entries)
    violations += _check_merged_group_classes(index, entries)
    violations += _check_resource_capacity(index, entries)
    violations += _check_full_class_occupancy(problem, index, entries)
    violations += _check_max_periods_per_day(problem, entries)

    return VerificationReport(passed=not violations, violations=tuple(violations))


def _check_teacher_non_overlap(entries) -> list[str]:
    seen: dict[tuple, list[ScheduleEntry]] = defaultdict(list)
    for e in entries:
        if e.teacher_id is not None:
            seen[(e.teacher_id, e.day_id, e.period_id)].append(e)
    return [
        f"Teacher {teacher_id!r} double-booked at ({day_id!r}, {period_id!r}): "
        f"{[x.activity_id for x in group]}"
        for (teacher_id, day_id, period_id), group in seen.items()
        if len(group) > 1
    ]


def _check_participant_group_non_overlap(entries) -> list[str]:
    seen: dict[tuple, list[ScheduleEntry]] = defaultdict(list)
    for e in entries:
        if e.participant_group_id is not None:
            seen[(e.participant_group_id, e.day_id, e.period_id)].append(e)
    return [
        f"Participant group {group_id!r} double-booked at ({day_id!r}, {period_id!r})"
        for (group_id, day_id, period_id), group in seen.items()
        if len(group) > 1
    ]


def _check_weekly_lesson_counts(problem: SchedulingProblem, entries) -> list[str]:
    counts: dict[str, int] = defaultdict(int)
    for e in entries:
        if e.requirement_id is not None:
            counts[e.requirement_id] += 1
    violations = []
    for req in problem.teaching_requirements:
        actual = counts.get(req.id, 0)
        if actual != req.weekly_periods:
            violations.append(
                f"Requirement {req.id!r} scheduled {actual} times, expected {req.weekly_periods}"
            )
    return violations


def _check_teacher_unavailable_respected(index: ProblemIndex, entries) -> list[str]:
    violations = []
    for e in entries:
        if e.teacher_id is None:
            continue
        status = index.get_availability(e.teacher_id, e.day_id, e.period_id)
        if status == AvailabilityStatus.UNAVAILABLE:
            violations.append(
                f"Teacher {e.teacher_id!r} scheduled at UNAVAILABLE slot ({e.day_id!r}, {e.period_id!r})"
            )
    return violations


def _check_fixed_placements_respected(problem: SchedulingProblem, entries) -> list[str]:
    scheduled = {
        (e.requirement_id, e.day_id, e.period_id)
        for e in entries
        if e.requirement_id is not None
    }
    violations = []
    for fp in problem.fixed_placements:
        key = (fp.requirement_id, fp.slot.day_id, fp.slot.period_id)
        if key not in scheduled:
            violations.append(
                f"Fixed placement {fp.id!r} for requirement {fp.requirement_id!r} not honored "
                f"at ({fp.slot.day_id!r}, {fp.slot.period_id!r})"
            )
    return violations


def _check_required_block_patterns(problem: SchedulingProblem, index: ProblemIndex, entries) -> list[str]:
    """Independently re-derive whether a REQUIRED lesson-block pattern
    (Phase 2A: an arbitrary multiset of positive block lengths, e.g.
    ``(2, 2)`` or ``(3, 1)``) actually holds, from the raw entries alone.

    Two independent facts are checked per requirement:
    1. The multiset of per-day lesson counts (days with zero lessons
       excluded) must exactly equal the sorted pattern -- this alone rules
       out both "wrong number of distinct days" and "wrong block sizes",
       since any merging or splitting of intended blocks across days would
       change this multiset.
    2. Every day whose count is > 1 must be a single genuinely consecutive,
       same-block_id run of periods -- ruling out two non-adjacent periods
       (or a pair crossing a structural break) masquerading as one block.
    """
    violations = []

    for req in problem.teaching_requirements:
        if req.block_policy.mode != BlockPolicyMode.REQUIRED:
            continue
        pattern = sorted(req.block_policy.block_sizes)
        if not pattern:
            continue  # malformed policy; already reported elsewhere

        periods_by_day: dict[str, list[str]] = defaultdict(list)
        for e in entries:
            if e.requirement_id == req.id:
                periods_by_day[e.day_id].append(e.period_id)

        actual_lengths = sorted(len(pids) for pids in periods_by_day.values() if pids)
        if actual_lengths != pattern:
            violations.append(
                f"Requirement {req.id!r} REQUIRED pattern {pattern!r} does not match actual "
                f"per-day lesson counts {actual_lengths!r}"
            )
            continue

        for day_id, period_ids in periods_by_day.items():
            if len(period_ids) <= 1:
                continue
            ordered = sorted(
                (index.periods_by_id[pid] for pid in period_ids), key=lambda p: p.index
            )
            for a, b in zip(ordered, ordered[1:]):
                if b.index != a.index + 1 or a.block_id != b.block_id:
                    violations.append(
                        f"Requirement {req.id!r} block on {day_id!r} is not a single consecutive "
                        f"same-block_id run: periods {[p.id for p in ordered]!r}"
                    )
                    break
    return violations


def _check_reserved_blocks_respected(problem: SchedulingProblem, entries) -> list[str]:
    violations = []
    reserved_entries_by_block: dict[str, set] = defaultdict(set)
    for e in entries:
        if e.source == EntrySource.RESERVED_BLOCK and e.reserved_block_id:
            reserved_entries_by_block[e.reserved_block_id].add((e.day_id, e.period_id))

    requirement_entries_by_class_slot: dict[tuple, list[ScheduleEntry]] = defaultdict(list)
    for e in entries:
        if e.requirement_id is not None:
            for class_id in e.class_sections:
                requirement_entries_by_class_slot[(class_id, e.day_id, e.period_id)].append(e)

    for block in problem.reserved_blocks:
        present_slots = reserved_entries_by_block.get(block.id, set())
        for slot in block.slots:
            if (slot.day_id, slot.period_id) not in present_slots:
                violations.append(
                    f"Reserved block {block.id!r} missing its own entry at "
                    f"({slot.day_id!r}, {slot.period_id!r})"
                )
            for class_id in block.class_sections:
                clashing = requirement_entries_by_class_slot.get((class_id, slot.day_id, slot.period_id), [])
                if clashing:
                    violations.append(
                        f"Class {class_id!r} has ordinary lesson(s) during reserved block "
                        f"{block.id!r} at ({slot.day_id!r}, {slot.period_id!r}): "
                        f"{[c.requirement_id for c in clashing]}"
                    )
    return violations


def _check_split_groups_synchronized(index: ProblemIndex, entries) -> list[str]:
    violations = []
    slots_by_requirement: dict[str, set] = defaultdict(set)
    for e in entries:
        if e.requirement_id is not None:
            slots_by_requirement[e.requirement_id].add((e.day_id, e.period_id))

    for req_ids in index.split_groups.values():
        slot_sets = [slots_by_requirement.get(rid, set()) for rid in req_ids]
        reference = slot_sets[0]
        for rid, slots in zip(req_ids, slot_sets):
            if slots != reference:
                violations.append(
                    f"Split branch {rid!r} slots {sorted(slots)} do not match "
                    f"branch {req_ids[0]!r} slots {sorted(reference)}"
                )
    return violations


def _check_merged_group_classes(index: ProblemIndex, entries) -> list[str]:
    violations = []
    for e in entries:
        if e.requirement_id is None:
            continue
        req = index.requirements_by_id[e.requirement_id]
        group = index.participant_groups_by_id[req.participant_group_id]
        if tuple(e.class_sections) != tuple(group.class_sections):
            violations.append(
                f"Entry for requirement {req.id!r} at ({e.day_id!r}, {e.period_id!r}) records "
                f"classes {e.class_sections!r}, expected {group.class_sections!r}"
            )
    return violations


def _check_resource_capacity(index: ProblemIndex, entries) -> list[str]:
    violations = []
    usage: dict[tuple, int] = defaultdict(int)
    for e in entries:
        if e.resource_id is not None:
            usage[(e.resource_id, e.day_id, e.period_id)] += 1
    for (resource_id, day_id, period_id), count in usage.items():
        capacity = index.resources_by_id[resource_id].capacity
        if count > capacity:
            violations.append(
                f"Resource {resource_id!r} used {count} times at ({day_id!r}, {period_id!r}), "
                f"capacity is {capacity}"
            )
    return violations


def _occupancy_unit_key(index: ProblemIndex, requirement_id: str) -> tuple:
    split_id = index.split_group_of_requirement.get(requirement_id)
    if split_id:
        return ("split", split_id)
    return ("req", requirement_id)


def _check_full_class_occupancy(problem: SchedulingProblem, index: ProblemIndex, entries) -> list[str]:
    violations = []
    units_by_class_slot: dict[tuple, set] = defaultdict(set)

    for e in entries:
        for class_id in e.class_sections:
            if e.source == EntrySource.RESERVED_BLOCK:
                key = ("reserved", e.reserved_block_id)
            else:
                key = _occupancy_unit_key(index, e.requirement_id)
            units_by_class_slot[(class_id, e.day_id, e.period_id)].add(key)

    for class_section in problem.class_sections:
        for day in index.days_sorted:
            for period in index.instructional_periods_sorted:
                units = units_by_class_slot.get((class_section.id, day.id, period.id), set())
                if len(units) == 0:
                    violations.append(
                        f"Class {class_section.id!r} has no activity at ({day.id!r}, {period.id!r})"
                    )
                elif len(units) > 1:
                    violations.append(
                        f"Class {class_section.id!r} double-booked at ({day.id!r}, {period.id!r}): {units!r}"
                    )
    return violations


def _check_max_periods_per_day(problem: SchedulingProblem, entries) -> list[str]:
    violations = []
    counts: dict[tuple, int] = defaultdict(int)
    for e in entries:
        if e.requirement_id is not None:
            counts[(e.requirement_id, e.day_id)] += 1

    for req in problem.teaching_requirements:
        max_per_day = req.distribution_policy.max_periods_per_day
        if max_per_day is None:
            continue
        for (req_id, day_id), count in counts.items():
            if req_id == req.id and count > max_per_day:
                violations.append(
                    f"Requirement {req.id!r} scheduled {count} times on {day_id!r}, "
                    f"max_periods_per_day is {max_per_day}"
                )
    return violations
