"""Explicit domain -> API mapping (Phase 3A2.4).

One pure function, `config_response_from_problem`, converting a frozen
`SchedulingProblem` into the hand-designed `SchedulingConfigResponse`
wire contract (`api/schemas.py`). Deliberately field-by-field, never
generic reflection/`dataclasses.asdict()` -- so a new domain field never
leaks into the public API contract without an explicit decision to add
it here too (see `docs/DECISIONS.md` #30).

Imports no SQLAlchemy, no `persistence.models`, no `fixtures/`, no
solver internals -- only `domain/` and this package's own schemas.
Every top-level and nested tuple here is a direct, order-preserving
`tuple(... for x in problem.<field>)` over the domain tuple already
handed to it -- this function never re-sorts or re-orders anything
itself; exact order is `SchedulingProblemRepository`'s responsibility
(Phase 3A2.3), already proven there.
"""
from __future__ import annotations

from school_timetable.api.schemas import (
    AcademicYearResponse,
    ActivityResponse,
    ClassSectionResponse,
    DayResponse,
    DistributionPolicyResponse,
    FixedPlacementResponse,
    LessonBlockPolicyResponse,
    ParticipantGroupResponse,
    PeriodResponse,
    ReservedBlockResponse,
    ResourceRequirementResponse,
    ResourceResponse,
    SchedulingConfigResponse,
    SchoolResponse,
    TeacherAvailabilityResponse,
    TeacherResponse,
    TeachingRequirementResponse,
    TimePreferenceResponse,
    TimeSlotResponse,
)
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import TeachingRequirement


def config_response_from_problem(problem: SchedulingProblem) -> SchedulingConfigResponse:
    return SchedulingConfigResponse(
        school=SchoolResponse(id=problem.school.id, name=problem.school.name),
        academic_year=AcademicYearResponse(id=problem.academic_year.id, label=problem.academic_year.label),
        days=tuple(DayResponse(id=d.id, name=d.name, index=d.index) for d in problem.days),
        periods=tuple(
            PeriodResponse(
                id=p.id, name=p.name, index=p.index, block_id=p.block_id,
                is_instructional=p.is_instructional,
            )
            for p in problem.periods
        ),
        teachers=tuple(TeacherResponse(id=t.id, name=t.name) for t in problem.teachers),
        class_sections=tuple(ClassSectionResponse(id=c.id, name=c.name) for c in problem.class_sections),
        participant_groups=tuple(
            ParticipantGroupResponse(id=g.id, name=g.name, class_sections=g.class_sections)
            for g in problem.participant_groups
        ),
        activities=tuple(
            ActivityResponse(id=a.id, name=a.name, kind=a.kind.value) for a in problem.activities
        ),
        teaching_requirements=tuple(
            _teaching_requirement_response(r) for r in problem.teaching_requirements
        ),
        resources=tuple(
            ResourceResponse(id=r.id, name=r.name, capacity=r.capacity) for r in problem.resources
        ),
        teacher_availabilities=tuple(
            TeacherAvailabilityResponse(
                teacher_id=a.teacher_id, day_id=a.day_id, period_id=a.period_id, status=a.status.value,
            )
            for a in problem.teacher_availabilities
        ),
        reserved_blocks=tuple(
            ReservedBlockResponse(
                id=b.id,
                name=b.name,
                activity_id=b.activity_id,
                class_sections=b.class_sections,
                slots=tuple(
                    TimeSlotResponse(day_id=s.day_id, period_id=s.period_id) for s in b.slots
                ),
                teacher_id=b.teacher_id,
            )
            for b in problem.reserved_blocks
        ),
        fixed_placements=tuple(
            FixedPlacementResponse(
                id=fp.id,
                requirement_id=fp.requirement_id,
                slot=TimeSlotResponse(day_id=fp.slot.day_id, period_id=fp.slot.period_id),
            )
            for fp in problem.fixed_placements
        ),
    )


def _teaching_requirement_response(requirement: TeachingRequirement) -> TeachingRequirementResponse:
    resource_requirement = requirement.resource_requirement
    return TeachingRequirementResponse(
        id=requirement.id,
        teacher_id=requirement.teacher_id,
        activity_id=requirement.activity_id,
        participant_group_id=requirement.participant_group_id,
        weekly_periods=requirement.weekly_periods,
        block_policy=LessonBlockPolicyResponse(
            mode=requirement.block_policy.mode.value,
            block_sizes=requirement.block_policy.block_sizes,
        ),
        distribution_policy=DistributionPolicyResponse(
            min_distinct_days=requirement.distribution_policy.min_distinct_days,
            max_periods_per_day=requirement.distribution_policy.max_periods_per_day,
        ),
        time_preferences=tuple(
            TimePreferenceResponse(preferred_periods=tp.preferred_periods, weight=tp.weight.value)
            for tp in requirement.time_preferences
        ),
        resource_requirement=(
            ResourceRequirementResponse(resource_id=resource_requirement.resource_id)
            if resource_requirement is not None
            else None
        ),
        split_group_id=requirement.split_group_id,
    )
