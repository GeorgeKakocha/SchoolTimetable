"""Solver-behavior regression tests for Owner Decision #38's exposed
`AvailabilityStatus` states -- proving, against small, deterministic,
purpose-built problems, both halves of the HARD/SOFT distinction the
design gate could only prove by code construction: `UNAVAILABLE` (via
a required consecutive block that must not cross it) and `PREFER_NOT`
(via its actual *effect* on solver choice, not just its enum
serialization). `test_unavailable_teacher_slots_never_used`
(`tests/test_solver_valid_fixture.py`) already proves the plain,
single-period HARD case against the full pilot fixture and is reused,
unmodified, as part of the existing regression suite -- not duplicated
here.

Every problem below declares a second, unconstrained "filler" teacher
(`t2`) whose own requirement (`r2`) occupies whichever periods `r1`
does not, purely to satisfy `_check_class_full_occupancy` (every class
must be fully occupied every instructional slot) without giving `r1`
itself any less placement freedom -- `t2` carries no availability
exceptions of its own, so it never affects which choice the solver
makes for `t1`/`r1`.
"""
from __future__ import annotations

from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear, Day, Period
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, LessonBlockPolicy, TeachingRequirement
from school_timetable.domain.result import SolverStatus
from school_timetable.domain.school import School
from school_timetable.scheduling.solver import solve

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)

_TWO_PERIOD_DAYS = (Day(id="d1", name="D1", index=0),)
_TWO_PERIODS = (
    Period(id="p1", name="P1", index=0, block_id="blk"),
    Period(id="p2", name="P2", index=1, block_id="blk"),
)

_R2_FILLER_TWO_SLOTS = TeachingRequirement(
    id="r2", teacher_id="t2", activity_id="a1", participant_group_id="pg1",
    weekly_periods=1, block_policy=FLEXIBLE,
)


def _minimal_problem(**overrides):
    defaults = dict(
        school=School(id="s", name="S"),
        academic_year=AcademicYear(id="ay", label="AY"),
        days=_TWO_PERIOD_DAYS,
        periods=_TWO_PERIODS,
        teachers=(
            Teacher(id="t1", first_name="T1", last_name=""),
            Teacher(id="t2", first_name="T2", last_name=""),
        ),
        class_sections=(ClassSection(id="c1", name="C1"),),
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
        ),
        activities=(Activity(id="a1", name="A1"),),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
            _R2_FILLER_TWO_SLOTS,
        ),
        teacher_availabilities=(),
    )
    defaults.update(overrides)
    return SchedulingProblem(**defaults)


def _placed_period(result, requirement_id="r1") -> str:
    entries = [e for e in result.entries if e.requirement_id == requirement_id]
    assert len(entries) == 1
    return entries[0].period_id


def test_prefer_not_avoided_when_equivalent_available_alternative_exists():
    """p1 is plain AVAILABLE; p2 is PREFER_NOT for `t1`. `t2`'s filler
    requirement is free to take either slot. With every other
    objective term equal, the weighted PREFER_NOT penalty must make
    CP-SAT strictly prefer p1 for `t1`'s lesson."""
    problem = _minimal_problem(
        teacher_availabilities=(TeacherAvailability("t1", "d1", "p2", AvailabilityStatus.PREFER_NOT),),
    )
    result = solve(problem)
    assert result.status == SolverStatus.OPTIMAL
    assert _placed_period(result) == "p1"


def test_prefer_not_slot_still_usable_when_it_is_the_only_feasible_option():
    """p1 is UNAVAILABLE (HARD) for `t1`; p2 is only PREFER_NOT (SOFT).
    `t1`'s lesson must still be placeable at p2 -- PREFER_NOT never
    forbids a placement, it only discourages it when a better
    alternative exists (and here there is none for `t1`)."""
    problem = _minimal_problem(
        teacher_availabilities=(
            TeacherAvailability("t1", "d1", "p1", AvailabilityStatus.UNAVAILABLE),
            TeacherAvailability("t1", "d1", "p2", AvailabilityStatus.PREFER_NOT),
        ),
    )
    result = solve(problem)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    assert _placed_period(result) == "p2"


_FOUR_PERIOD_DAYS = (Day(id="d1", name="D1", index=0),)
_FOUR_PERIODS = (
    Period(id="p1", name="P1", index=0, block_id="blk"),
    Period(id="p2", name="P2", index=1, block_id="blk"),
    Period(id="p3", name="P3", index=2, block_id="blk"),
    Period(id="p4", name="P4", index=3, block_id="blk"),
)


def test_required_consecutive_block_never_crosses_unavailable_period():
    """A required double-lesson block for `t1`, who is UNAVAILABLE at
    p2, has only one remaining valid consecutive pair -- (p3, p4),
    since both (p1, p2) and (p2, p3) touch the unavailable slot. `t2`'s
    filler requirement (2 unconstrained single periods) fills whatever
    `t1`'s block does not. The solver must place `t1`'s block at
    (p3, p4), never partially or fully at p2."""
    problem = _minimal_problem(
        days=_FOUR_PERIOD_DAYS,
        periods=_FOUR_PERIODS,
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=2,
                block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2,)),
            ),
            TeachingRequirement(
                id="r2", teacher_id="t2", activity_id="a1", participant_group_id="pg1",
                weekly_periods=2, block_policy=FLEXIBLE,
            ),
        ),
        teacher_availabilities=(TeacherAvailability("t1", "d1", "p2", AvailabilityStatus.UNAVAILABLE),),
    )
    result = solve(problem)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    placed_periods = {e.period_id for e in result.entries if e.requirement_id == "r1"}
    assert placed_periods == {"p3", "p4"}
