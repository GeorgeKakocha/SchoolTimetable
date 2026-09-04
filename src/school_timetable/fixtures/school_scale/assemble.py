"""Assembles a SchedulingProblem from a Curriculum.

This is where the known-feasibility guarantee is actually earned: a
preliminary problem (with no fixed placements yet) is built directly from
the curriculum's own decisions and solved once (see ``constructor.py``) to
obtain a genuine witness. Only a small, explicitly chosen subset of that
witness is promoted to real ``FixedPlacement`` objects; the witness itself
is discarded once feasibility is confirmed and is never reused by the
benchmark that later solves the final problem fresh.
"""
from __future__ import annotations

from dataclasses import dataclass

from school_timetable.domain.blocks import FixedPlacement
from school_timetable.domain.calendar import AcademicYear, TimeSlot
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import TeachingRequirement
from school_timetable.domain.resources import ResourceRequirement
from school_timetable.domain.school import School
from school_timetable.fixtures.common import build_days, build_periods
from school_timetable.fixtures.school_scale.consistency import check_class_occupancy_consistency
from school_timetable.fixtures.school_scale.constructor import CanonicalSchedule, FixtureGenerationError, build_canonical_schedule
from school_timetable.fixtures.school_scale.curriculum import (
    FIXED_PLACEMENT_SOURCE_SUBJECTS,
    NUM_FIXED_PLACEMENTS,
    Curriculum,
)


@dataclass
class AssembledSchoolScaleFixture:
    problem: SchedulingProblem
    canonical_schedule: CanonicalSchedule
    stats: dict


def build_problem_from_curriculum(
    curriculum: Curriculum, fixed_placements: tuple[FixedPlacement, ...] = ()
) -> SchedulingProblem:
    days = build_days()
    periods = build_periods()

    teaching_requirements = tuple(
        TeachingRequirement(
            id=req.id, teacher_id=req.teacher_ids[0], activity_id=req.activity_id,
            participant_group_id=req.participant_group_id, weekly_periods=req.weekly_periods,
            block_policy=req.block_policy, distribution_policy=req.distribution_policy,
            time_preferences=req.time_preferences,
            resource_requirement=ResourceRequirement(resource_id=req.resource_id) if req.resource_id else None,
            split_group_id=req.split_group_id,
        )
        for req in curriculum.requirements
    )

    return SchedulingProblem(
        school=School(id="synthetic-school-scale", name="Synthetic School-Scale Pilot"),
        academic_year=AcademicYear(id="ay-2026", label="2026/2027"),
        days=days, periods=periods,
        teachers=curriculum.teachers, class_sections=curriculum.class_sections,
        participant_groups=curriculum.participant_groups, activities=curriculum.activities,
        teaching_requirements=teaching_requirements, resources=curriculum.resources,
        teacher_availabilities=curriculum.teacher_availabilities, reserved_blocks=curriculum.reserved_blocks,
        fixed_placements=fixed_placements,
    )


def assemble(curriculum: Curriculum, *, num_fixed_placements: int = NUM_FIXED_PLACEMENTS) -> AssembledSchoolScaleFixture:
    # Step 1: prove feasibility with no fixed placements yet.
    preliminary_problem = build_problem_from_curriculum(curriculum, fixed_placements=())
    canonical = build_canonical_schedule(preliminary_problem)

    # Step 2: derive a handful of genuine fixed placements from the witness.
    candidates = [
        req for req in curriculum.requirements
        if req.split_group_id is None and len(req.class_ids) == 1
        and req.activity_id.replace("subject_", "") in FIXED_PLACEMENT_SOURCE_SUBJECTS
    ]
    candidates.sort(key=lambda r: r.id)
    step = max(1, len(candidates) // max(1, num_fixed_placements))
    chosen = candidates[::step][:num_fixed_placements]

    fixed_placements = tuple(
        FixedPlacement(id=f"fixed_{i:02d}", requirement_id=req.id, slot=TimeSlot(*canonical.placements[req.id][0]))
        for i, req in enumerate(chosen)
    )

    # Step 3: build the final problem the benchmark will actually solve.
    problem = build_problem_from_curriculum(curriculum, fixed_placements=fixed_placements)

    occupancy_report = check_class_occupancy_consistency(problem)
    if not occupancy_report.passed:
        raise FixtureGenerationError(
            "Generated school-scale problem fails class occupancy consistency: "
            + "; ".join(occupancy_report.mismatches)
        )

    by_split_group: dict[str, int] = {}
    for req in curriculum.requirements:
        if req.split_group_id:
            by_split_group[req.split_group_id] = by_split_group.get(req.split_group_id, 0) + 1

    stats = {
        "num_classes": len(problem.class_sections),
        "num_teachers": len(problem.teachers),
        "num_activities": len(problem.activities),
        "num_requirements": len(problem.teaching_requirements),
        "num_participant_groups": len(problem.participant_groups),
        "num_reserved_blocks": len(problem.reserved_blocks),
        "num_fixed_placements": len(problem.fixed_placements),
        "num_split_groups": len(by_split_group),
        "num_merged_requirements": sum(1 for r in curriculum.requirements if len(r.class_ids) > 1),
        "total_class_slot_units": len(problem.class_sections) * len(problem.days) * len(
            [p for p in problem.periods if p.is_instructional]
        ),
    }

    return AssembledSchoolScaleFixture(problem=problem, canonical_schedule=canonical, stats=stats)
