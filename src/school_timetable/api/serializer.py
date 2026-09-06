"""Explicit domain/application -> API mapping (Phase 3A2.4, extended
Phase 3A3.4).

One pure function, `config_response_from_problem`, converting a frozen
`SchedulingProblem` into the hand-designed `SchedulingConfigResponse`
wire contract (`api/schemas.py`). Deliberately field-by-field, never
generic reflection/`dataclasses.asdict()` -- so a new domain field never
leaks into the public API contract without an explicit decision to add
it here too (see `docs/DECISIONS.md` #30).

Imports no SQLAlchemy, no `persistence.models`, no `fixtures/`, no
solver internals -- only `domain/`, `application/`, and this package's
own schemas. Every top-level and nested tuple here is a direct,
order-preserving `tuple(... for x in ...)` over the tuple already
handed to it -- this module never re-sorts or re-orders anything
itself; exact order is the caller's (`SchedulingProblemRepository`/
`ScheduleVersionRepository`) responsibility, already proven at that
layer. These functions never query a database and never recreate any
scheduling/preflight logic -- `ActiveScheduleVersion.entries` already
carries every field a `ScheduleEntryResponse` needs, fully resolved.
"""
from __future__ import annotations

from school_timetable.api.schemas import (
    AcademicYearResponse,
    ActiveScheduleResponse,
    ActivityResponse,
    ClassSectionResponse,
    ClassTimetableCellResponse,
    ClassTimetableEntryResponse,
    ClassTimetableResponse,
    ClassTimetableRowResponse,
    DayHeaderResponse,
    DayResponse,
    DistributionPolicyResponse,
    FixedPlacementResponse,
    GenerateScheduleResponse,
    LessonBlockPolicyResponse,
    ParticipantGroupResponse,
    PeriodResponse,
    ReservedBlockResponse,
    ResourceRequirementResponse,
    ResourceResponse,
    ScheduleEntryResponse,
    SchedulingConfigResponse,
    SchoolResponse,
    TeacherAvailabilityResponse,
    TeacherResponse,
    TeachingRequirementResponse,
    TimePreferenceResponse,
    TimeSlotResponse,
    ValidationDiagnosticResponse,
)
from school_timetable.application.class_timetable_models import ClassTimetableEntry, ClassTimetableView
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import TeachingRequirement
from school_timetable.domain.result import ScheduleEntry
from school_timetable.validation.errors import ValidationError


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


# -- Schedule generation/read API (Phase 3A3.4). -------------------------


def generate_response_from_active_version(version: ActiveScheduleVersion) -> GenerateScheduleResponse:
    """`POST .../schedule/generate`'s success body -- no `entries` field
    at all, per the locked contract."""
    return GenerateScheduleResponse(
        version_number=version.version_number,
        solver_status=version.solver_status.value,
        total_soft_penalty=version.total_soft_penalty,
        created_at=version.created_at,
        is_active=True,
    )


def schedule_entry_response_from_entry(entry: ScheduleEntry) -> ScheduleEntryResponse:
    """Verbatim field-by-field copy -- never re-derives, never fixes up
    a malformed entry (e.g. a `source` inconsistent with which of
    `requirement_id`/`reserved_block_id` is populated); that would be a
    genuine upstream defect this function must not silently paper over."""
    return ScheduleEntryResponse(
        source=entry.source.value,
        day_id=entry.day_id,
        period_id=entry.period_id,
        requirement_id=entry.requirement_id,
        reserved_block_id=entry.reserved_block_id,
        activity_id=entry.activity_id,
        teacher_id=entry.teacher_id,
        participant_group_id=entry.participant_group_id,
        resource_id=entry.resource_id,
        class_sections=entry.class_sections,
    )


def active_schedule_response_from_active_version(version: ActiveScheduleVersion) -> ActiveScheduleResponse:
    """`GET .../schedule/active`'s success body. `version.entries` is
    already in exact persisted `ordinal` order (`ScheduleVersionRepository`'s
    responsibility, already proven) -- this function preserves that
    order verbatim, never re-sorting."""
    return ActiveScheduleResponse(
        version_number=version.version_number,
        solver_status=version.solver_status.value,
        total_soft_penalty=version.total_soft_penalty,
        created_at=version.created_at,
        is_active=True,
        entries=tuple(schedule_entry_response_from_entry(e) for e in version.entries),
    )


def validation_diagnostic_response_from_error(error: ValidationError) -> ValidationDiagnosticResponse:
    return ValidationDiagnosticResponse(code=error.code, message=error.message, context=error.context)


# -- Class-timetable projection API (Phase 3B.1). ------------------------


def class_timetable_response_from_view(view: ClassTimetableView) -> ClassTimetableResponse:
    """Pure application-view-model -> Pydantic conversion only -- all
    class-membership filtering, cell grouping, calendar ordering, and
    name resolution already happened in `ClassTimetableService`
    (Decision #32 Owner Decision 8). This function never re-sorts,
    re-groups, or re-resolves anything; it preserves `view.days`/
    `view.rows`/each row's `cells`/each cell's `entries` order exactly."""
    return ClassTimetableResponse(
        school_id=view.school_id,
        school_name=view.school_name,
        academic_year_id=view.academic_year_id,
        academic_year_label=view.academic_year_label,
        class_section_id=view.class_section_id,
        class_section_name=view.class_section_name,
        version_number=view.version_number,
        solver_status=view.solver_status.value,
        total_soft_penalty=view.total_soft_penalty,
        created_at=view.created_at,
        is_active=view.is_active,
        days=tuple(DayHeaderResponse(id=d.id, name=d.name) for d in view.days),
        rows=tuple(
            ClassTimetableRowResponse(
                period_id=row.period_id,
                period_name=row.period_name,
                cells=tuple(
                    ClassTimetableCellResponse(
                        day_id=cell.day_id,
                        entries=tuple(_class_timetable_entry_response(e) for e in cell.entries),
                    )
                    for cell in row.cells
                ),
            )
            for row in view.rows
        ),
    )


def _class_timetable_entry_response(entry: ClassTimetableEntry) -> ClassTimetableEntryResponse:
    return ClassTimetableEntryResponse(
        source=entry.source.value,
        activity_id=entry.activity_id,
        activity_name=entry.activity_name,
        teacher_id=entry.teacher_id,
        teacher_name=entry.teacher_name,
        participant_group_id=entry.participant_group_id,
        participant_group_name=entry.participant_group_name,
        requirement_id=entry.requirement_id,
        reserved_block_id=entry.reserved_block_id,
        resource_id=entry.resource_id,
    )
