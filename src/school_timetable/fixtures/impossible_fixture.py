"""A deliberately infeasible synthetic fixture.

One teacher is asked for more weekly periods than they have usable slots
for (after accounting for their UNAVAILABLE slots). This must be rejected
by preflight validation (TEACHER_OVERLOADED) before CP-SAT ever runs.
"""
from __future__ import annotations

from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear
from school_timetable.domain.groups import ClassSection, ParticipantGroup
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, LessonBlockPolicy, TeachingRequirement
from school_timetable.domain.school import School
from school_timetable.fixtures.common import build_days, build_periods

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)


def build_impossible_fixture() -> SchedulingProblem:
    days = build_days()
    periods = build_periods()

    school = School(id="synthetic-school", name="Synthetic Pilot School")
    academic_year = AcademicYear(id="ay-2026", label="2026/2027")

    class_sections = (ClassSection(id="z", name="Z"),)
    participant_groups = (ParticipantGroup(id="pg_z", name="All of Z", class_sections=("z",)),)

    teachers = (
        Teacher(id="t_normal", name="Teacher Normal"),
        Teacher(id="t_overloaded", name="Teacher Overloaded"),
    )
    activities = (
        Activity(id="filler", name="Filler Subject"),
        Activity(id="core", name="Core Subject"),
    )

    # Unavailable for all of Friday (8 slots) and Thursday afternoon
    # (4 slots) = 12 unavailable slots out of 40, leaving 28 usable slots.
    unavailable_slots = [("fri", f"p{i}") for i in range(1, 9)] + [
        ("thu", f"p{i}") for i in range(5, 9)
    ]
    teacher_availabilities = tuple(
        TeacherAvailability("t_overloaded", day_id, period_id, AvailabilityStatus.UNAVAILABLE)
        for day_id, period_id in unavailable_slots
    )

    teaching_requirements = (
        TeachingRequirement(
            id="filler_z", teacher_id="t_normal", activity_id="filler", participant_group_id="pg_z",
            weekly_periods=10, block_policy=FLEXIBLE,
        ),
        # 30 required weekly periods, but only 28 usable slots remain for
        # this teacher -- infeasible by construction.
        TeachingRequirement(
            id="core_z", teacher_id="t_overloaded", activity_id="core", participant_group_id="pg_z",
            weekly_periods=30, block_policy=FLEXIBLE,
        ),
    )

    return SchedulingProblem(
        school=school,
        academic_year=academic_year,
        days=days,
        periods=periods,
        teachers=teachers,
        class_sections=class_sections,
        participant_groups=participant_groups,
        activities=activities,
        teaching_requirements=teaching_requirements,
        teacher_availabilities=teacher_availabilities,
    )
