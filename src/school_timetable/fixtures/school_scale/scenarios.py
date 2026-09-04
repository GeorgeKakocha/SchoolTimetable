"""Phase 2B school-scale scenarios: standard, tight, and impossible.

``standard`` and ``tight`` share the exact same curriculum shape (classes,
subjects, teachers, splits, merges, clubs) -- ``tight`` only dials down
scheduling freedom (more teacher unavailability, more pinned fixed
lessons) via the same generator, so both are proven feasible by the
identical known-feasible-construction mechanism.

``impossible`` is deliberately NOT run through the feasibility-proving
constructor (a witness cannot exist for something genuinely infeasible).
Instead it takes the already-proven-feasible ``standard`` problem and
mutates only resource demand, which is the cleanest way to guarantee every
other reference/consistency fact remains valid -- isolating the
introduced defect to exactly the one bottleneck being tested.
"""
from __future__ import annotations

import dataclasses

from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.school_scale.assemble import AssembledSchoolScaleFixture, assemble
from school_timetable.fixtures.school_scale.curriculum import NUM_FIXED_PLACEMENTS, build_curriculum


def build_school_scale_standard() -> AssembledSchoolScaleFixture:
    curriculum = build_curriculum(extra_unavailability=False)
    return assemble(curriculum, num_fixed_placements=NUM_FIXED_PLACEMENTS)


def build_school_scale_tight() -> AssembledSchoolScaleFixture:
    """A more constrained but still known-feasible variant: broader teacher
    unavailability and roughly twice as many pinned fixed lessons."""
    curriculum = build_curriculum(extra_unavailability=True)
    return assemble(curriculum, num_fixed_placements=NUM_FIXED_PLACEMENTS + 6)


def build_school_scale_impossible() -> SchedulingProblem:
    """A deliberate, realistic-scale contradiction: doubles every
    sport/dance requirement's weekly_periods on top of the proven-feasible
    standard problem, pushing total capacity-1 gym demand from 30 to 60
    weekly periods against a 40-slot ceiling -- a genuine, unavoidable
    resource bottleneck.

    To isolate that single defect, an equal amount is trimmed from each
    affected class's art requirement (a FLEXIBLE subject with no resource
    or pattern constraints, and -- at 3 periods/class -- enough slack to
    absorb the largest possible per-class gym increase of 2), so every
    reference stays valid and class occupancy remains internally
    consistent -- this must still pass preflight's
    reference/occupancy/teacher-load checks (none of which reasons about
    resource-capacity-vs-total-slots) and only be caught by CP-SAT as
    INFEASIBLE. See docs/SCALE_VALIDATION.md.
    """
    standard = build_school_scale_standard()
    problem = standard.problem

    gym_increase_by_class: dict[str, int] = {}
    new_requirements = []
    for req in problem.teaching_requirements:
        if req.resource_requirement is not None:
            increase = req.weekly_periods  # doubling adds exactly one more copy of the current load
            req = dataclasses.replace(req, weekly_periods=req.weekly_periods * 2)
            group = next(g for g in problem.participant_groups if g.id == req.participant_group_id)
            for class_id in group.class_sections:
                gym_increase_by_class[class_id] = gym_increase_by_class.get(class_id, 0) + increase
        new_requirements.append(req)

    final_requirements = []
    for req in new_requirements:
        if req.activity_id == "subject_art" and len(
            next(g for g in problem.participant_groups if g.id == req.participant_group_id).class_sections
        ) == 1:
            group = next(g for g in problem.participant_groups if g.id == req.participant_group_id)
            trim = sum(gym_increase_by_class.get(c, 0) for c in group.class_sections)
            new_weekly = req.weekly_periods - trim
            assert new_weekly > 0, f"art requirement {req.id!r} trimmed to non-positive weekly_periods"
            final_requirements.append(dataclasses.replace(req, weekly_periods=new_weekly))
        else:
            final_requirements.append(req)

    return dataclasses.replace(problem, teaching_requirements=tuple(final_requirements))
