from school_timetable.domain.activities import Activity
from school_timetable.domain.blocks import FixedPlacement
from school_timetable.domain.calendar import TimeSlot, AcademicYear
from school_timetable.domain.groups import ClassSection, ParticipantGroup
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    LessonBlockPolicy,
    TeachingRequirement,
)
from school_timetable.domain.resources import ResourceRequirement
from school_timetable.domain.school import School
from school_timetable.fixtures.common import build_days, build_periods
from school_timetable.fixtures.impossible_fixture import build_impossible_fixture
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.validation.preflight import run_preflight

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)


def _base_problem(**overrides):
    days = build_days()
    periods = build_periods()
    defaults = dict(
        school=School(id="s", name="S"),
        academic_year=AcademicYear(id="ay", label="AY"),
        days=days,
        periods=periods,
        teachers=(Teacher(id="t1", name="T1"),),
        class_sections=(ClassSection(id="c1", name="C1"),),
        participant_groups=(ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",)),),
        activities=(Activity(id="a1", name="A1"),),
        teaching_requirements=(),
    )
    defaults.update(overrides)
    return SchedulingProblem(**defaults)


def test_valid_fixture_has_no_preflight_errors():
    assert run_preflight(build_valid_fixture()) == []


def test_impossible_fixture_flags_teacher_overload():
    errors = run_preflight(build_impossible_fixture())
    codes = {e.code for e in errors}
    assert "TEACHER_OVERLOADED" in codes


def test_unknown_participant_group_reference():
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1",
                participant_group_id="does-not-exist", weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "UNKNOWN_PARTICIPANT_GROUP" in codes


def test_unknown_resource_reference():
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
                resource_requirement=ResourceRequirement(resource_id="no-such-resource"),
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "UNKNOWN_RESOURCE" in codes


def test_block_pattern_total_mismatch():
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=5,
                block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2, 1, 1)),
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "BLOCK_PATTERN_TOTAL_MISMATCH" in codes


def test_unsupported_block_size_is_rejected():
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=3,
                block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(3,)),
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "UNSUPPORTED_BLOCK_SIZE" in codes


def test_fixed_placement_on_unavailable_slot_is_rejected():
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
        teacher_availabilities=(
            TeacherAvailability("t1", "mon", "p1", AvailabilityStatus.UNAVAILABLE),
        ),
        fixed_placements=(
            FixedPlacement(id="fp1", requirement_id="r1", slot=TimeSlot("mon", "p1")),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "FIXED_PLACEMENT_TEACHER_UNAVAILABLE" in codes


def test_class_occupancy_mismatch_detected():
    # Only 1 weekly period declared for a class that has 40 instructional
    # slots -- nowhere near full occupancy.
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "CLASS_OCCUPANCY_MISMATCH" in codes


def test_split_group_weekly_periods_mismatch_detected():
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",)),
            ParticipantGroup(id="pg2", name="PG2", class_sections=("c1",)),
        ),
        teachers=(Teacher(id="t1", name="T1"), Teacher(id="t2", name="T2")),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=3, block_policy=FLEXIBLE, split_group_id="split1",
            ),
            TeachingRequirement(
                id="r2", teacher_id="t2", activity_id="a1", participant_group_id="pg2",
                weekly_periods=4, block_policy=FLEXIBLE, split_group_id="split1",
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "SPLIT_GROUP_WEEKLY_PERIODS_MISMATCH" in codes
