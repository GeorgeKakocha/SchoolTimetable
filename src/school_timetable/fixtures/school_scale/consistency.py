"""Independent fixture-level occupancy consistency check (Phase 2B).

Deliberately separate from ``validation.preflight``'s own
``CLASS_OCCUPANCY_MISMATCH`` check: this exists so fixture generation (and
its own unit tests) can assert full occupancy directly, as
fixture-authoring infrastructure, before the solver -- or even preflight
-- ever runs. It re-derives the same fact independently rather than
calling into preflight's internals, so the two checks are a genuine
double-confirmation, not one check wearing two names.
"""
from __future__ import annotations

from dataclasses import dataclass

from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.problem import SchedulingProblem


@dataclass(frozen=True)
class OccupancyConsistencyReport:
    passed: bool
    mismatches: tuple[str, ...] = ()


def check_class_occupancy_consistency(problem: SchedulingProblem) -> OccupancyConsistencyReport:
    """For every class: expected weekly occupancy (sum of weekly_periods
    from occupancy-contributing requirements, with synchronized split
    branches counted once, plus reserved-block coverage) must equal the
    number of configured instructional slots."""
    index = ProblemIndex(problem)
    total_slots = len(index.all_slots)

    lesson_load: dict[str, int] = {c.id: 0 for c in problem.class_sections}
    for req in problem.teaching_requirements:
        if not index.is_split_branch_representative(req):
            continue  # synchronized sibling branch -- already counted once
        group = index.participant_groups_by_id[req.participant_group_id]
        for class_id in group.class_sections:
            if class_id in lesson_load:
                lesson_load[class_id] += req.weekly_periods

    reserved_load: dict[str, int] = {c.id: 0 for c in problem.class_sections}
    for block in problem.reserved_blocks:
        for class_id in block.class_sections:
            if class_id in reserved_load:
                reserved_load[class_id] += len(block.slots)

    mismatches = []
    for class_id in lesson_load:
        total = lesson_load[class_id] + reserved_load[class_id]
        if total != total_slots:
            mismatches.append(
                f"Class {class_id!r}: expected weekly occupancy {total_slots}, got {total} "
                f"(lessons={lesson_load[class_id]}, reserved={reserved_load[class_id]})"
            )

    return OccupancyConsistencyReport(passed=not mismatches, mismatches=tuple(mismatches))
