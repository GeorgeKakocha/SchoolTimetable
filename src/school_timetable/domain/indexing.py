"""Pure-python lookup structures derived from a SchedulingProblem.

Shared by preflight validation, the CP-SAT model builder, and the
independent verifier so that "how do I look up X" is defined exactly once.
Contains no OR-Tools dependency.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from school_timetable.domain.calendar import Day, Period, TimeSlot, consecutive_period_pairs
from school_timetable.domain.people import AvailabilityStatus
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import TeachingRequirement


@dataclass
class ProblemIndex:
    problem: SchedulingProblem

    days_by_id: dict = field(default_factory=dict)
    periods_by_id: dict = field(default_factory=dict)
    teachers_by_id: dict = field(default_factory=dict)
    activities_by_id: dict = field(default_factory=dict)
    class_sections_by_id: dict = field(default_factory=dict)
    participant_groups_by_id: dict = field(default_factory=dict)
    resources_by_id: dict = field(default_factory=dict)
    requirements_by_id: dict = field(default_factory=dict)

    days_sorted: tuple[Day, ...] = ()
    instructional_periods_sorted: tuple[Period, ...] = ()
    consecutive_pairs: list[tuple[Period, Period]] = field(default_factory=list)
    all_slots: list[TimeSlot] = field(default_factory=list)

    availability: dict[tuple[str, str, str], AvailabilityStatus] = field(default_factory=dict)

    reserved_class_slots: dict[tuple[str, str, str], str] = field(default_factory=dict)
    """(class_id, day_id, period_id) -> reserved_block_id"""

    reserved_teacher_slots: dict[tuple[str, str, str], str] = field(default_factory=dict)
    """(teacher_id, day_id, period_id) -> reserved_block_id"""

    reserved_resource_usage: dict[tuple[str, str, str], int] = field(default_factory=dict)
    """(resource_id, day_id, period_id) -> count of DISTINCT ReservedBlocks
    using that Resource in that slot (Resources B2). Each ReservedBlock
    contributes exactly 1 regardless of how many `class_sections` it
    has -- one block occupying one Resource once per slot, never once
    per participating class. Shared by preflight (reserved-vs-reserved
    structural validity), the CP-SAT model builder (fixed usage that
    pre-consumes capacity before ordinary lessons), and the independent
    verifier (via final `ScheduleEntry.resource_id`) so the aggregate
    capacity invariant is defined exactly once."""

    split_groups: dict[str, list[str]] = field(default_factory=dict)
    """split_group_id -> requirement ids, sorted deterministically"""

    split_group_of_requirement: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        p = self.problem
        self.days_by_id = {d.id: d for d in p.days}
        self.periods_by_id = {pe.id: pe for pe in p.periods}
        self.teachers_by_id = {t.id: t for t in p.teachers}
        self.activities_by_id = {a.id: a for a in p.activities}
        self.class_sections_by_id = {c.id: c for c in p.class_sections}
        self.participant_groups_by_id = {g.id: g for g in p.participant_groups}
        self.resources_by_id = {r.id: r for r in p.resources}
        self.requirements_by_id = {r.id: r for r in p.teaching_requirements}

        self.days_sorted = tuple(sorted(p.days, key=lambda d: d.index))
        self.instructional_periods_sorted = tuple(
            sorted((pe for pe in p.periods if pe.is_instructional), key=lambda pe: pe.index)
        )
        self.consecutive_pairs = consecutive_period_pairs(self.instructional_periods_sorted)
        self.all_slots = [
            TimeSlot(d.id, pe.id) for d in self.days_sorted for pe in self.instructional_periods_sorted
        ]

        for avail in p.teacher_availabilities:
            self.availability[(avail.teacher_id, avail.day_id, avail.period_id)] = avail.status

        for block in p.reserved_blocks:
            for slot in block.slots:
                for class_id in block.class_sections:
                    self.reserved_class_slots[(class_id, slot.day_id, slot.period_id)] = block.id
                if block.teacher_id:
                    self.reserved_teacher_slots[(block.teacher_id, slot.day_id, slot.period_id)] = block.id
                if block.resource_id:
                    usage_key = (block.resource_id, slot.day_id, slot.period_id)
                    self.reserved_resource_usage[usage_key] = self.reserved_resource_usage.get(usage_key, 0) + 1

        groups: dict[str, list[str]] = defaultdict(list)
        for req in p.teaching_requirements:
            if req.split_group_id:
                groups[req.split_group_id].append(req.id)
        for split_id, req_ids in groups.items():
            req_ids_sorted = sorted(req_ids)
            self.split_groups[split_id] = req_ids_sorted
            for rid in req_ids_sorted:
                self.split_group_of_requirement[rid] = split_id

    def get_availability(self, teacher_id: str, day_id: str, period_id: str) -> AvailabilityStatus:
        return self.availability.get((teacher_id, day_id, period_id), AvailabilityStatus.AVAILABLE)

    def is_reserved_for_class(self, class_id: str, day_id: str, period_id: str) -> bool:
        return (class_id, day_id, period_id) in self.reserved_class_slots

    def is_reserved_for_teacher(self, teacher_id: str, day_id: str, period_id: str) -> bool:
        return (teacher_id, day_id, period_id) in self.reserved_teacher_slots

    def reserved_resource_usage_at(self, resource_id: str, day_id: str, period_id: str) -> int:
        return self.reserved_resource_usage.get((resource_id, day_id, period_id), 0)

    def split_group_representative(self, split_group_id: str) -> str:
        """The requirement whose slot-variables count toward class occupancy
        for the whole split block (branches are always synchronized, so
        counting more than one would double-count occupancy)."""
        return self.split_groups[split_group_id][0]

    def is_split_branch_representative(self, requirement: TeachingRequirement) -> bool:
        if not requirement.split_group_id:
            return True
        return self.split_group_representative(requirement.split_group_id) == requirement.id
