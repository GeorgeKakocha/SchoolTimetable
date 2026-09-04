"""A deterministic, mathematically feasible synthetic fixture.

All identifiers are synthetic (no real school data). This fixture is
designed to exercise every hard/soft constraint required by the phase-1
solver contract in one coherent, fully-occupied timetable for 4 classes:

- a REQUIRED double lesson (Math, class 8a)
- a PREFERRED double lesson (Science, class 8b)
- a teacher with UNAVAILABLE slots (t_science)
- a teacher PREFER_NOT slot (t_history)
- a German/Russian split block running in parallel (class 8a)
- a merged lesson spanning two classes (History, 9a + 9b)
- two reserved Club blocks on different days for different classes
- Sport and Dance both requiring the capacity-1 Indoor Gym resource
- one fixed placement (Art, class 8b)
- a preferred-period rule (History, class 8a)
- a max_periods_per_day rule (Science, class 9a)
- a min_distinct_days soft preference (History, class 8a)

Every main class (8a, 8b, 9a, 9b) has its weekly workload sized to exactly
fill all 40 instructional slots (5 days x 8 periods) once reserved blocks
and split/merged occupancy are accounted for.
"""
from __future__ import annotations

from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.blocks import FixedPlacement, ReservedBlock
from school_timetable.domain.calendar import AcademicYear, TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    DistributionPolicy,
    LessonBlockPolicy,
    PreferenceWeight,
    TeachingRequirement,
    TimePreference,
)
from school_timetable.domain.resources import Resource, ResourceRequirement
from school_timetable.domain.school import School
from school_timetable.fixtures.common import build_days, build_periods

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)


def build_valid_fixture() -> SchedulingProblem:
    days = build_days()
    periods = build_periods()

    school = School(id="synthetic-school", name="Synthetic Pilot School")
    academic_year = AcademicYear(id="ay-2026", label="2026/2027")

    class_sections = (
        ClassSection(id="8a", name="8-A"),
        ClassSection(id="8b", name="8-B"),
        ClassSection(id="9a", name="9-A"),
        ClassSection(id="9b", name="9-B"),
    )

    teachers = (
        Teacher(id="t_math", name="Teacher Math"),
        Teacher(id="t_science", name="Teacher Science"),
        Teacher(id="t_history", name="Teacher History"),
        Teacher(id="t_art", name="Teacher Art"),
        Teacher(id="t_sport", name="Teacher Sport"),
        Teacher(id="t_dance", name="Teacher Dance"),
        Teacher(id="t_german", name="Teacher German"),
        Teacher(id="t_russian", name="Teacher Russian"),
    )

    activities = (
        Activity(id="math", name="Mathematics"),
        Activity(id="science", name="Science"),
        Activity(id="history", name="History"),
        Activity(id="art", name="Art"),
        Activity(id="sport", name="Sport"),
        Activity(id="dance", name="Dance"),
        Activity(id="german", name="German"),
        Activity(id="russian", name="Russian"),
        Activity(id="club_chess", name="Chess Club", kind=ActivityKind.CLUB),
        Activity(id="club_robotics", name="Robotics Club", kind=ActivityKind.CLUB),
    )

    resources = (
        Resource(id="gym", name="Indoor Gym", capacity=1),
    )

    participant_groups = (
        ParticipantGroup(id="pg_8a", name="All of 8-A", class_sections=("8a",)),
        ParticipantGroup(id="pg_8b", name="All of 8-B", class_sections=("8b",)),
        ParticipantGroup(id="pg_9a", name="All of 9-A", class_sections=("9a",)),
        ParticipantGroup(id="pg_9b", name="All of 9-B", class_sections=("9b",)),
        ParticipantGroup(id="pg_8a_german", name="8-A German", class_sections=("8a",)),
        ParticipantGroup(id="pg_8a_russian", name="8-A Russian", class_sections=("8a",)),
        ParticipantGroup(id="pg_9a_9b_merged", name="9-A + 9-B Merged History", class_sections=("9a", "9b")),
    )

    teacher_availabilities = (
        # t_science: one teacher with UNAVAILABLE slots.
        TeacherAvailability("t_science", "fri", "p7", AvailabilityStatus.UNAVAILABLE),
        TeacherAvailability("t_science", "fri", "p8", AvailabilityStatus.UNAVAILABLE),
        # t_history: one PREFER_NOT slot (soft).
        TeacherAvailability("t_history", "tue", "p3", AvailabilityStatus.PREFER_NOT),
    )

    gym_requirement = ResourceRequirement(resource_id="gym")

    teaching_requirements = (
        # -- 8-A --------------------------------------------------------
        TeachingRequirement(
            id="math_8a", teacher_id="t_math", activity_id="math", participant_group_id="pg_8a",
            weekly_periods=5,
            block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2, 1, 1, 1)),
        ),
        TeachingRequirement(
            id="science_8a", teacher_id="t_science", activity_id="science", participant_group_id="pg_8a",
            weekly_periods=9, block_policy=FLEXIBLE,
        ),
        TeachingRequirement(
            id="history_8a", teacher_id="t_history", activity_id="history", participant_group_id="pg_8a",
            weekly_periods=9, block_policy=FLEXIBLE,
            distribution_policy=DistributionPolicy(min_distinct_days=5),
            time_preferences=(TimePreference(preferred_periods=(0, 1), weight=PreferenceWeight.MEDIUM),),
        ),
        TeachingRequirement(
            id="art_8a", teacher_id="t_art", activity_id="art", participant_group_id="pg_8a",
            weekly_periods=9, block_policy=FLEXIBLE,
        ),
        TeachingRequirement(
            id="sport_8a", teacher_id="t_sport", activity_id="sport", participant_group_id="pg_8a",
            weekly_periods=2, block_policy=FLEXIBLE, resource_requirement=gym_requirement,
        ),
        TeachingRequirement(
            id="dance_8a", teacher_id="t_dance", activity_id="dance", participant_group_id="pg_8a",
            weekly_periods=2, block_policy=FLEXIBLE, resource_requirement=gym_requirement,
        ),
        TeachingRequirement(
            id="german_8a", teacher_id="t_german", activity_id="german",
            participant_group_id="pg_8a_german", weekly_periods=3, block_policy=FLEXIBLE,
            split_group_id="split_lang_8a",
        ),
        TeachingRequirement(
            id="russian_8a", teacher_id="t_russian", activity_id="russian",
            participant_group_id="pg_8a_russian", weekly_periods=3, block_policy=FLEXIBLE,
            split_group_id="split_lang_8a",
        ),

        # -- 8-B --------------------------------------------------------
        TeachingRequirement(
            id="math_8b", teacher_id="t_math", activity_id="math", participant_group_id="pg_8b",
            weekly_periods=8, block_policy=FLEXIBLE,
        ),
        TeachingRequirement(
            id="science_8b", teacher_id="t_science", activity_id="science", participant_group_id="pg_8b",
            weekly_periods=5,
            block_policy=LessonBlockPolicy(BlockPolicyMode.PREFERRED, block_sizes=(2, 1, 1, 1)),
        ),
        TeachingRequirement(
            id="history_8b", teacher_id="t_history", activity_id="history", participant_group_id="pg_8b",
            weekly_periods=13, block_policy=FLEXIBLE,
        ),
        TeachingRequirement(
            id="art_8b", teacher_id="t_art", activity_id="art", participant_group_id="pg_8b",
            weekly_periods=9, block_policy=FLEXIBLE,
        ),
        TeachingRequirement(
            id="sport_8b", teacher_id="t_sport", activity_id="sport", participant_group_id="pg_8b",
            weekly_periods=2, block_policy=FLEXIBLE, resource_requirement=gym_requirement,
        ),
        TeachingRequirement(
            id="dance_8b", teacher_id="t_dance", activity_id="dance", participant_group_id="pg_8b",
            weekly_periods=2, block_policy=FLEXIBLE, resource_requirement=gym_requirement,
        ),

        # -- 9-A --------------------------------------------------------
        TeachingRequirement(
            id="math_9a", teacher_id="t_math", activity_id="math", participant_group_id="pg_9a",
            weekly_periods=13, block_policy=FLEXIBLE,
        ),
        TeachingRequirement(
            id="science_9a", teacher_id="t_science", activity_id="science", participant_group_id="pg_9a",
            weekly_periods=11, block_policy=FLEXIBLE,
            distribution_policy=DistributionPolicy(max_periods_per_day=3),
        ),
        TeachingRequirement(
            id="art_9a", teacher_id="t_art", activity_id="art", participant_group_id="pg_9a",
            weekly_periods=10, block_policy=FLEXIBLE,
        ),
        TeachingRequirement(
            id="sport_9a", teacher_id="t_sport", activity_id="sport", participant_group_id="pg_9a",
            weekly_periods=2, block_policy=FLEXIBLE, resource_requirement=gym_requirement,
        ),
        TeachingRequirement(
            id="dance_9a", teacher_id="t_dance", activity_id="dance", participant_group_id="pg_9a",
            weekly_periods=2, block_policy=FLEXIBLE, resource_requirement=gym_requirement,
        ),

        # -- 9-B --------------------------------------------------------
        TeachingRequirement(
            id="math_9b", teacher_id="t_math", activity_id="math", participant_group_id="pg_9b",
            weekly_periods=13, block_policy=FLEXIBLE,
        ),
        TeachingRequirement(
            id="science_9b", teacher_id="t_science", activity_id="science", participant_group_id="pg_9b",
            weekly_periods=11, block_policy=FLEXIBLE,
        ),
        TeachingRequirement(
            id="art_9b", teacher_id="t_art", activity_id="art", participant_group_id="pg_9b",
            weekly_periods=10, block_policy=FLEXIBLE,
        ),
        TeachingRequirement(
            id="sport_9b", teacher_id="t_sport", activity_id="sport", participant_group_id="pg_9b",
            weekly_periods=2, block_policy=FLEXIBLE, resource_requirement=gym_requirement,
        ),
        TeachingRequirement(
            id="dance_9b", teacher_id="t_dance", activity_id="dance", participant_group_id="pg_9b",
            weekly_periods=2, block_policy=FLEXIBLE, resource_requirement=gym_requirement,
        ),

        # -- Merged: 9-A + 9-B combined History lesson -------------------
        TeachingRequirement(
            id="history_merged_9a_9b", teacher_id="t_history", activity_id="history",
            participant_group_id="pg_9a_9b_merged", weekly_periods=1, block_policy=FLEXIBLE,
        ),
    )

    reserved_blocks = (
        ReservedBlock(
            id="club_chess", name="Chess Club", activity_id="club_chess",
            class_sections=("8a", "8b"), slots=(TimeSlot("wed", "p8"),),
        ),
        ReservedBlock(
            id="club_robotics", name="Robotics Club", activity_id="club_robotics",
            class_sections=("9a", "9b"), slots=(TimeSlot("thu", "p8"),),
        ),
    )

    fixed_placements = (
        FixedPlacement(id="fixed_art_8b", requirement_id="art_8b", slot=TimeSlot("mon", "p1")),
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
        resources=resources,
        teacher_availabilities=teacher_availabilities,
        reserved_blocks=reserved_blocks,
        fixed_placements=fixed_placements,
    )
