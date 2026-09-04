"""Converts a solved CP-SAT model back into domain-level result objects."""
from __future__ import annotations

from ortools.sat.python import cp_model

from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.scheduling.model_builder import BuiltModel


def build_schedule_entries(
    problem: SchedulingProblem,
    built: BuiltModel,
    solver: cp_model.CpSolver,
) -> tuple[ScheduleEntry, ...]:
    index = built.index
    entries: list[ScheduleEntry] = []

    for req in problem.teaching_requirements:
        group = index.participant_groups_by_id[req.participant_group_id]
        for day in index.days_sorted:
            for period in index.instructional_periods_sorted:
                var = built.lesson_vars[(req.id, day.id, period.id)]
                if solver.Value(var) == 1:
                    entries.append(ScheduleEntry(
                        source=EntrySource.REQUIREMENT,
                        activity_id=req.activity_id,
                        day_id=day.id,
                        period_id=period.id,
                        class_sections=group.class_sections,
                        teacher_id=req.teacher_id,
                        participant_group_id=req.participant_group_id,
                        resource_id=req.resource_requirement.resource_id if req.resource_requirement else None,
                        requirement_id=req.id,
                    ))

    for block in problem.reserved_blocks:
        for slot in block.slots:
            entries.append(ScheduleEntry(
                source=EntrySource.RESERVED_BLOCK,
                activity_id=block.activity_id,
                day_id=slot.day_id,
                period_id=slot.period_id,
                class_sections=block.class_sections,
                teacher_id=block.teacher_id,
                participant_group_id=None,
                reserved_block_id=block.id,
            ))

    return tuple(entries)
