"""Unit tests proving the independent verifier actually detects violations
rather than trivially agreeing with whatever it is given. These build
ScheduleEntry lists by hand -- no CP-SAT involved.
"""
from __future__ import annotations

from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, LessonBlockPolicy, TeachingRequirement
from school_timetable.domain.resources import Resource, ResourceRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.domain.school import School
from school_timetable.fixtures.common import build_days, build_periods
from school_timetable.verification.verifier import verify

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)


def _tiny_problem(**overrides) -> SchedulingProblem:
    days = build_days()
    periods = build_periods()
    defaults = dict(
        school=School(id="s", name="S"),
        academic_year=AcademicYear(id="ay", label="AY"),
        days=days,
        periods=periods,
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection(id="c1", name="C1"),),
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup(id="pg2", name="PG2", class_sections=("c1",), role=ParticipantGroupRole.SUBGROUP),
        ),
        activities=(Activity(id="a1", name="A1"), Activity(id="a2", name="A2")),
        resources=(Resource(id="gym", name="Gym", capacity=1),),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=2, block_policy=FLEXIBLE,
            ),
            TeachingRequirement(
                id="r2", teacher_id="t2", activity_id="a2", participant_group_id="pg2",
                weekly_periods=2, block_policy=FLEXIBLE,
            ),
        ),
    )
    defaults.update(overrides)
    return SchedulingProblem(**defaults)


def _entry(requirement, day_id, period_id, teacher_id, group_id, resource_id=None):
    return ScheduleEntry(
        source=EntrySource.REQUIREMENT,
        activity_id=requirement.activity_id,
        day_id=day_id,
        period_id=period_id,
        class_sections=("c1",),
        teacher_id=teacher_id,
        participant_group_id=group_id,
        requirement_id=requirement.id,
        resource_id=resource_id,
    )


def test_verifier_detects_teacher_overlap():
    problem = _tiny_problem()
    r1, r2 = problem.teaching_requirements
    # Both requirements use different teachers in the fixture; force an
    # overlap by (incorrectly) using t1 for both at the same slot.
    entries = (
        _entry(r1, "mon", "p1", "t1", "pg1"),
        _entry(r2, "mon", "p1", "t1", "pg2"),
    )
    report = verify(problem, entries)
    assert not report.passed
    assert any("double-booked" in v for v in report.violations)


def test_verifier_detects_weekly_count_mismatch():
    problem = _tiny_problem()
    r1, r2 = problem.teaching_requirements
    # r1 requires 2 weekly periods but only 1 is scheduled here.
    entries = (
        _entry(r1, "mon", "p1", "t1", "pg1"),
        _entry(r2, "mon", "p1", "t2", "pg2"),
        _entry(r2, "tue", "p1", "t2", "pg2"),
    )
    report = verify(problem, entries)
    assert not report.passed
    assert any("scheduled 1 times, expected 2" in v for v in report.violations)


def test_verifier_detects_resource_capacity_violation():
    gym = ResourceRequirement(resource_id="gym")
    problem = _tiny_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE, resource_requirement=gym,
            ),
            TeachingRequirement(
                id="r2", teacher_id="t2", activity_id="a2", participant_group_id="pg2",
                weekly_periods=1, block_policy=FLEXIBLE, resource_requirement=gym,
            ),
        ),
    )
    r1, r2 = problem.teaching_requirements
    entries = (
        _entry(r1, "mon", "p1", "t1", "pg1", resource_id="gym"),
        _entry(r2, "mon", "p1", "t2", "pg2", resource_id="gym"),
    )
    report = verify(problem, entries)
    assert not report.passed
    assert any("capacity is 1" in v for v in report.violations)


def test_verifier_passes_a_genuinely_correct_schedule():
    problem = _tiny_problem()
    r1, r2 = problem.teaching_requirements
    entries = (
        _entry(r1, "mon", "p1", "t1", "pg1"),
        _entry(r1, "tue", "p1", "t1", "pg1"),
        _entry(r2, "wed", "p1", "t2", "pg2"),
        _entry(r2, "thu", "p1", "t2", "pg2"),
    )
    # NOTE: this problem's classes are not fully occupied (only 2 of 40
    # slots filled), so full-occupancy violations are expected; strip them
    # out to isolate the checks this test cares about.
    report = verify(problem, entries)
    non_occupancy = [v for v in report.violations if "no activity at" not in v]
    assert non_occupancy == []
