"""Shared construction helper for Phase 2C editing tests.

Not a test module (no ``test_`` prefix) -- pytest will not collect it.

``validate_move`` now independently verifies the input ``Schedule`` is
HARD-valid before reasoning about a move (see ``scheduling/editing.py``),
which means every focused test problem must be genuinely fully occupied,
not just "the two or three entries this particular test cares about".
``fill_occupancy`` tops up whatever a test already declared with one inert
FLEXIBLE filler requirement per class -- using a dedicated filler
teacher/group so it can never interact with the specific HARD rule under
test -- covering exactly the slots not already used for that class.
"""
from __future__ import annotations

import dataclasses

from school_timetable.domain.activities import Activity
from school_timetable.domain.groups import ParticipantGroup
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, LessonBlockPolicy, TeachingRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)
FILLER_ACTIVITY_ID = "filler_activity"


def fill_occupancy(
    problem: SchedulingProblem, entries: tuple[ScheduleEntry, ...],
) -> tuple[SchedulingProblem, tuple[ScheduleEntry, ...]]:
    days_sorted = sorted(problem.days, key=lambda d: d.index)
    periods_sorted = sorted((p for p in problem.periods if p.is_instructional), key=lambda p: p.index)
    all_slots = [(d.id, p.id) for d in days_sorted for p in periods_sorted]

    teachers = list(problem.teachers)
    groups = list(problem.participant_groups)
    activities = list(problem.activities)
    requirements = list(problem.teaching_requirements)
    new_entries = list(entries)

    if not any(a.id == FILLER_ACTIVITY_ID for a in activities):
        activities.append(Activity(FILLER_ACTIVITY_ID, "Filler"))

    for i, class_section in enumerate(problem.class_sections):
        used = {(e.day_id, e.period_id) for e in entries if class_section.id in e.class_sections}
        free = [s for s in all_slots if s not in used]
        if not free:
            continue
        teacher_id = f"filler_teacher_{i}"
        group_id = f"filler_group_{i}"
        req_id = f"filler_req_{i}"
        teachers.append(Teacher(teacher_id, f"Filler Teacher {i}"))
        groups.append(ParticipantGroup(group_id, f"Filler Group {i}", (class_section.id,)))
        requirements.append(TeachingRequirement(
            req_id, teacher_id, FILLER_ACTIVITY_ID, group_id, len(free), FLEXIBLE,
        ))
        for day_id, period_id in free:
            new_entries.append(ScheduleEntry(
                source=EntrySource.REQUIREMENT, activity_id=FILLER_ACTIVITY_ID, day_id=day_id, period_id=period_id,
                class_sections=(class_section.id,), teacher_id=teacher_id, participant_group_id=group_id,
                requirement_id=req_id,
            ))

    filled_problem = dataclasses.replace(
        problem,
        teachers=tuple(teachers), participant_groups=tuple(groups),
        activities=tuple(activities), teaching_requirements=tuple(requirements),
    )
    return filled_problem, tuple(new_entries)
