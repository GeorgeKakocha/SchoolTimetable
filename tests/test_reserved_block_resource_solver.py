"""Solver-behavior regression tests for Resources B2's cross-source
aggregate Resource capacity invariant: fixed `ReservedBlock` usage
pre-consumes a Resource's capacity, and only what remains is available
to ordinary `TeachingRequirement` lesson placement --

    reserved_fixed_usage + ordinary_scheduled_usage <= capacity

never a pairwise "two things can't share a slot" rule, which would be
wrong whenever `capacity > 1` (proven explicitly by Case B below).

Every problem below declares filler requirements purely to satisfy
`_check_class_full_occupancy` (every class must be fully occupied every
instructional slot) without giving the requirement under test any less
placement freedom -- mirrors `test_teacher_availability_solver.py`'s
own established minimal-problem convention exactly.
"""
from __future__ import annotations

from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.blocks import ReservedBlock
from school_timetable.domain.calendar import AcademicYear, Day, Period, TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, LessonBlockPolicy, TeachingRequirement
from school_timetable.domain.resources import Resource, ResourceRequirement
from school_timetable.domain.result import SolverStatus
from school_timetable.domain.school import School
from school_timetable.scheduling.solver import solve
from school_timetable.validation.preflight import run_preflight

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)

_ONE_DAY = (Day(id="d1", name="D1", index=0),)
_TWO_PERIODS = (
    Period(id="p1", name="P1", index=0, block_id="blk"),
    Period(id="p2", name="P2", index=1, block_id="blk"),
)


def _minimal_problem(**overrides):
    defaults = dict(
        school=School(id="s", name="S"),
        academic_year=AcademicYear(id="ay", label="AY"),
        days=_ONE_DAY,
        periods=_TWO_PERIODS,
        teachers=(
            Teacher(id="t1", first_name="T1", last_name=""),
            Teacher(id="t2", first_name="T2", last_name=""),
            Teacher(id="t3", first_name="T3", last_name=""),
        ),
        class_sections=(ClassSection(id="c1", name="C1"), ClassSection(id="c2", name="C2")),
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup(id="pg2", name="PG2", class_sections=("c2",), role=ParticipantGroupRole.WHOLE_CLASS),
        ),
        activities=(Activity(id="a1", name="A1"), Activity(id="club1", name="Club1", kind=ActivityKind.CLUB)),
        resources=(Resource(id="gym", name="Gym", capacity=1),),
        reserved_blocks=(),
        teaching_requirements=(),
        teacher_availabilities=(),
    )
    defaults.update(overrides)
    return SchedulingProblem(**defaults)


def _placed_period(result, requirement_id="r1") -> str:
    entries = [e for e in result.entries if e.requirement_id == requirement_id]
    assert len(entries) == 1
    return entries[0].period_id


# -- Case A: capacity exhausted by a Reserved Activity -----------------

def test_capacity_exhausted_by_reserved_activity_forces_ordinary_lesson_elsewhere():
    """gym has capacity=1. A ReservedBlock fixes gym at (d1, p1) for c2
    (a DIFFERENT class from r1's own c1, so no class-overlap constraint
    forces this by itself). r1 (c1) also needs gym -- with p1's one unit
    of gym capacity already consumed by the reserved block, r1 must be
    scheduled at p2, never p1."""
    problem = _minimal_problem(
        reserved_blocks=(
            ReservedBlock(
                id="rb1", name="Club1", activity_id="club1", class_sections=("c2",),
                slots=(TimeSlot("d1", "p1"),), teacher_id=None, resource_id="gym",
            ),
        ),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE, resource_requirement=ResourceRequirement("gym"),
            ),
            TeachingRequirement(
                id="r1_filler", teacher_id="t2", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
            TeachingRequirement(
                id="r2_filler", teacher_id="t3", activity_id="a1", participant_group_id="pg2",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    assert run_preflight(problem) == []
    result = solve(problem)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    assert _placed_period(result, "r1") == "p2"


# -- Case B: remaining capacity still usable ----------------------------

def test_remaining_capacity_after_reserved_activity_is_still_usable():
    """gym has capacity=2. The same ReservedBlock as Case A fixes ONE
    unit of gym at (d1, p1) for c2 -- one unit remains available there
    for an ordinary lesson. `t1` is UNAVAILABLE at p2, so r1's only
    feasible slot is p1: the solve must still succeed and place r1
    there, proving the remaining capacity is genuinely usable, not
    wastefully blocked out just because the Resource is reserved at
    all in that slot (capacity=1 semantics would incorrectly forbid
    this)."""
    problem = _minimal_problem(
        resources=(Resource(id="gym", name="Gym", capacity=2),),
        reserved_blocks=(
            ReservedBlock(
                id="rb1", name="Club1", activity_id="club1", class_sections=("c2",),
                slots=(TimeSlot("d1", "p1"),), teacher_id=None, resource_id="gym",
            ),
        ),
        teacher_availabilities=(TeacherAvailability("t1", "d1", "p2", AvailabilityStatus.UNAVAILABLE),),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE, resource_requirement=ResourceRequirement("gym"),
            ),
            TeachingRequirement(
                id="r1_filler", teacher_id="t2", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
            TeachingRequirement(
                id="r2_filler", teacher_id="t3", activity_id="a1", participant_group_id="pg2",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    assert run_preflight(problem) == []
    result = solve(problem)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    assert _placed_period(result, "r1") == "p1"


# -- Case C: the fixed reserved configuration itself is invalid ---------

def test_two_conflicting_reserved_activities_caught_by_preflight_not_the_solver():
    """gym has capacity=1. Two DIFFERENT fixed ReservedBlocks both claim
    it at the identical (d1, p1) slot -- a structurally invalid
    configuration preflight must reject as HARD before generation ever
    reaches CP-SAT (`generate_schedule_service.py` always runs preflight
    first). This is never a case the solver itself could catch or
    should be relied on to catch: `ReservedBlock`s are fixed input, not
    CP-SAT decision variables, so `model_builder._add_resource_capacity`
    never even inspects reserved-vs-reserved usage -- it only
    constrains ordinary lesson variables against whatever capacity
    remains. Preflight is therefore the ONLY layer that can ever
    diagnose this."""
    problem = _minimal_problem(
        reserved_blocks=(
            ReservedBlock(
                id="rb1", name="Club1", activity_id="club1", class_sections=("c1",),
                slots=(TimeSlot("d1", "p1"),), teacher_id=None, resource_id="gym",
            ),
            ReservedBlock(
                id="rb2", name="Club1", activity_id="club1", class_sections=("c2",),
                slots=(TimeSlot("d1", "p1"),), teacher_id=None, resource_id="gym",
            ),
        ),
        teaching_requirements=(
            TeachingRequirement(
                id="r1_filler", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
            TeachingRequirement(
                id="r2_filler", teacher_id="t2", activity_id="a1", participant_group_id="pg2",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    errors = run_preflight(problem)
    codes = {e.code for e in errors}
    assert "RESERVED_RESOURCE_CAPACITY_EXCEEDED" in codes
