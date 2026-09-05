"""TEST-ONLY domain -> persistence aggregate writer (Phase 3A2.3).

Exists solely to prove the production persistence -> domain read path
(`persistence/problem_repository.py`) against a real PostgreSQL, by
writing a complete in-memory `SchedulingProblem` (e.g.
`build_valid_fixture()`) into the database first, then handing the
resulting rows to the production repository to read back.

This is NOT a production repository "save" method: it lives only under
`tests_web/`, is never imported by `persistence/`, `application/`, or
`api/`, and performs the natural-id -> surrogate-id identity resolution
across the whole graph that a real domain -> persistence write path
would need dedicated application-level design for -- see
`docs/DECISIONS.md` #28.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from school_timetable.domain.problem import SchedulingProblem
from school_timetable.persistence import models as orm


def write_scheduling_problem(session: Session, problem: SchedulingProblem) -> None:
    """Insert the complete `problem` into `session` in dependency order,
    assigning `ordinal` from `enumerate(...)` for every top-level tuple
    and every nested tuple, so the round-trip proof can hold the writer
    to preserving exact order rather than assuming it."""
    school_row = orm.School(natural_id=problem.school.id, name=problem.school.name)
    session.add(school_row)
    session.flush()

    year_row = orm.AcademicYear(
        school_id=school_row.id, natural_id=problem.academic_year.id, label=problem.academic_year.label,
    )
    session.add(year_row)
    session.flush()
    year_id = year_row.id

    day_ids: dict[str, int] = {}
    for day in problem.days:
        row = orm.Day(academic_year_id=year_id, natural_id=day.id, name=day.name, idx=day.index)
        session.add(row)
        session.flush()
        day_ids[day.id] = row.id

    period_ids: dict[str, int] = {}
    for period in problem.periods:
        row = orm.Period(
            academic_year_id=year_id, natural_id=period.id, name=period.name, idx=period.index,
            block_id=period.block_id, is_instructional=period.is_instructional,
        )
        session.add(row)
        session.flush()
        period_ids[period.id] = row.id

    class_section_ids: dict[str, int] = {}
    for ordinal, class_section in enumerate(problem.class_sections):
        row = orm.ClassSection(
            academic_year_id=year_id, natural_id=class_section.id, name=class_section.name, ordinal=ordinal,
        )
        session.add(row)
        session.flush()
        class_section_ids[class_section.id] = row.id

    teacher_ids: dict[str, int] = {}
    for ordinal, teacher in enumerate(problem.teachers):
        row = orm.Teacher(academic_year_id=year_id, natural_id=teacher.id, name=teacher.name, ordinal=ordinal)
        session.add(row)
        session.flush()
        teacher_ids[teacher.id] = row.id

    activity_ids: dict[str, int] = {}
    for ordinal, activity in enumerate(problem.activities):
        row = orm.Activity(
            academic_year_id=year_id, natural_id=activity.id, name=activity.name,
            kind=activity.kind.value, ordinal=ordinal,
        )
        session.add(row)
        session.flush()
        activity_ids[activity.id] = row.id

    resource_ids: dict[str, int] = {}
    for ordinal, resource in enumerate(problem.resources):
        row = orm.Resource(
            academic_year_id=year_id, natural_id=resource.id, name=resource.name,
            capacity=resource.capacity, ordinal=ordinal,
        )
        session.add(row)
        session.flush()
        resource_ids[resource.id] = row.id

    participant_group_ids: dict[str, int] = {}
    for ordinal, group in enumerate(problem.participant_groups):
        row = orm.ParticipantGroup(
            academic_year_id=year_id, natural_id=group.id, name=group.name, ordinal=ordinal,
        )
        session.add(row)
        session.flush()
        participant_group_ids[group.id] = row.id
        for member_ordinal, class_id in enumerate(group.class_sections):
            session.add(orm.ParticipantGroupClassSection(
                academic_year_id=year_id, participant_group_id=row.id,
                class_section_id=class_section_ids[class_id], ordinal=member_ordinal,
            ))
    session.flush()

    teaching_requirement_ids: dict[str, int] = {}
    for ordinal, requirement in enumerate(problem.teaching_requirements):
        resource_requirement = requirement.resource_requirement
        row = orm.TeachingRequirement(
            academic_year_id=year_id,
            natural_id=requirement.id,
            teacher_id=teacher_ids[requirement.teacher_id],
            activity_id=activity_ids[requirement.activity_id],
            participant_group_id=participant_group_ids[requirement.participant_group_id],
            weekly_periods=requirement.weekly_periods,
            block_mode=requirement.block_policy.mode.value,
            block_sizes=list(requirement.block_policy.block_sizes),
            min_distinct_days=requirement.distribution_policy.min_distinct_days,
            max_periods_per_day=requirement.distribution_policy.max_periods_per_day,
            resource_id=(
                resource_ids[resource_requirement.resource_id]
                if resource_requirement is not None
                else None
            ),
            split_group_id=requirement.split_group_id,
            ordinal=ordinal,
        )
        session.add(row)
        session.flush()
        teaching_requirement_ids[requirement.id] = row.id
        for tp_ordinal, time_pref in enumerate(requirement.time_preferences):
            session.add(orm.TimePreference(
                academic_year_id=year_id, teaching_requirement_id=row.id, ordinal=tp_ordinal,
                preferred_period_indexes=list(time_pref.preferred_periods), weight=time_pref.weight.value,
            ))
    session.flush()

    for ordinal, availability in enumerate(problem.teacher_availabilities):
        session.add(orm.TeacherAvailability(
            academic_year_id=year_id,
            teacher_id=teacher_ids[availability.teacher_id],
            day_id=day_ids[availability.day_id],
            period_id=period_ids[availability.period_id],
            status=availability.status.value,
            ordinal=ordinal,
        ))

    for ordinal, block in enumerate(problem.reserved_blocks):
        row = orm.ReservedBlock(
            academic_year_id=year_id, natural_id=block.id, name=block.name,
            activity_id=activity_ids[block.activity_id],
            teacher_id=(teacher_ids[block.teacher_id] if block.teacher_id is not None else None),
            ordinal=ordinal,
        )
        session.add(row)
        session.flush()
        for cs_ordinal, class_id in enumerate(block.class_sections):
            session.add(orm.ReservedBlockClassSection(
                academic_year_id=year_id, reserved_block_id=row.id,
                class_section_id=class_section_ids[class_id], ordinal=cs_ordinal,
            ))
        for slot_ordinal, slot in enumerate(block.slots):
            session.add(orm.ReservedBlockSlot(
                academic_year_id=year_id, reserved_block_id=row.id,
                day_id=day_ids[slot.day_id], period_id=period_ids[slot.period_id], ordinal=slot_ordinal,
            ))

    for ordinal, fixed_placement in enumerate(problem.fixed_placements):
        session.add(orm.FixedPlacement(
            academic_year_id=year_id, natural_id=fixed_placement.id,
            teaching_requirement_id=teaching_requirement_ids[fixed_placement.requirement_id],
            day_id=day_ids[fixed_placement.slot.day_id], period_id=period_ids[fixed_placement.slot.period_id],
            ordinal=ordinal,
        ))

    session.flush()
