"""Phase 2C: re-optimization around a reference schedule.

Scenarios A-D from the Phase 2C brief, each with a small, deterministic,
fully-occupied problem (reoptimize requires full class occupancy, since it
runs the same preflight as a fresh solve).
"""
from __future__ import annotations

import dataclasses

from school_timetable.domain.activities import Activity
from school_timetable.domain.blocks import FixedPlacement, ReservedBlock
from school_timetable.domain.calendar import AcademicYear, Day, Period, TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    DistributionPolicy,
    LessonBlockPolicy,
    TeachingRequirement,
)
from school_timetable.domain.resources import Resource, ResourceRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry, SolverStatus
from school_timetable.domain.schedule import OccurrenceKey, Schedule
from school_timetable.domain.school import School
from school_timetable.scheduling.editing import lock_occurrence
from school_timetable.scheduling.reoptimize import reoptimize
from school_timetable.verification.verifier import verify

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)

DAYS = tuple(Day(id=f"d{i}", name=f"Day{i}", index=i - 1) for i in range(1, 4))
PERIODS = (
    Period(id="q1", name="Q1", index=0, block_id="am"),
    Period(id="q2", name="Q2", index=1, block_id="am"),
)


def _entry(requirement_id, activity_id, day_id, period_id, teacher_id, group_id):
    return ScheduleEntry(
        source=EntrySource.REQUIREMENT, activity_id=activity_id, day_id=day_id, period_id=period_id,
        class_sections=("cx",), teacher_id=teacher_id, participant_group_id=group_id,
        requirement_id=requirement_id,
    )


def _six_single_period_problem(**overrides) -> SchedulingProblem:
    """3 days x 2 periods = 6 slots, 6 requirements of weekly_periods=1
    each -- exactly fills class cx. Simple building block for all four
    re-optimization scenarios."""
    teachers = tuple(Teacher(f"t{i}", f"T{i}") for i in range(1, 7))
    groups = tuple(
        ParticipantGroup(
            f"pg{i}", f"PG{i}", ("cx",),
            ParticipantGroupRole.WHOLE_CLASS if i == 1 else ParticipantGroupRole.SUBGROUP,
        )
        for i in range(1, 7)
    )
    activities = tuple(Activity(f"subj{i}", f"Subj{i}") for i in range(1, 7))
    requirements = tuple(
        TeachingRequirement(f"r{i}", f"t{i}", f"subj{i}", f"pg{i}", 1, FLEXIBLE) for i in range(1, 7)
    )
    defaults = dict(
        school=School(id="s", name="S"), academic_year=AcademicYear(id="ay", label="AY"),
        days=DAYS, periods=PERIODS,
        teachers=teachers, class_sections=(ClassSection("cx", "CX"),),
        participant_groups=groups, activities=activities, teaching_requirements=requirements,
        resources=(), teacher_availabilities=(), reserved_blocks=(), fixed_placements=(),
    )
    defaults.update(overrides)
    return SchedulingProblem(**defaults)


def _reference_entries():
    return (
        _entry("r1", "subj1", "d1", "q1", "t1", "pg1"),
        _entry("r2", "subj2", "d1", "q2", "t2", "pg2"),
        _entry("r3", "subj3", "d2", "q1", "t3", "pg3"),
        _entry("r4", "subj4", "d2", "q2", "t4", "pg4"),
        _entry("r5", "subj5", "d3", "q1", "t5", "pg5"),
        _entry("r6", "subj6", "d3", "q2", "t6", "pg6"),
    )


def _entry_slots(entries):
    return {(e.requirement_id, e.day_id, e.period_id) for e in entries}


# ---------------------------------------------------------------------------
# A. No forced change -> reference schedule fully preserved.
# ---------------------------------------------------------------------------

def test_no_forced_change_preserves_reference_schedule():
    problem = _six_single_period_problem()
    schedule = Schedule(entries=_reference_entries())

    result = reoptimize(problem, schedule)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    assert result.metadata["num_moved_occurrences"] == 0
    assert result.metadata["num_preserved_occurrences"] == 6
    assert _entry_slots(result.entries) == _entry_slots(schedule.entries)

    report = verify(problem, result.entries)
    assert report.passed, report.violations


# ---------------------------------------------------------------------------
# B. One forced change -> exactly the affected occurrence moves.
# ---------------------------------------------------------------------------

def test_one_forced_change_moves_only_the_affected_occurrence():
    problem = _six_single_period_problem(
        teacher_availabilities=(TeacherAvailability("t1", "d1", "q1", AvailabilityStatus.UNAVAILABLE),),
    )
    schedule = Schedule(entries=_reference_entries())

    result = reoptimize(problem, schedule)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    report = verify(problem, result.entries)
    assert report.passed, report.violations

    # r1 can no longer sit at d1/q1, so it must swap with whatever occupies
    # its new slot -- the proven optimum for this exact 6-slot fixture is
    # exactly 2 moved occurrences (r1 and its swap partner), never more.
    assert result.metadata["num_moved_occurrences"] == 2
    assert result.metadata["disruption_penalty"] == result.metadata["num_moved_occurrences"]

    r1_slot = [(e.day_id, e.period_id) for e in result.entries if e.requirement_id == "r1"]
    assert r1_slot != [("d1", "q1")]

    # Confirm the teacher's UNAVAILABLE slot is genuinely respected.
    for e in result.entries:
        if e.teacher_id == "t1":
            assert (e.day_id, e.period_id) != ("d1", "q1")


# ---------------------------------------------------------------------------
# C. Locked surroundings -> locks remain exact, valid alternative found.
# ---------------------------------------------------------------------------

def test_locked_surroundings_preserved_while_unlocked_occurrence_moves():
    problem = _six_single_period_problem(
        teacher_availabilities=(TeacherAvailability("t1", "d1", "q1", AvailabilityStatus.UNAVAILABLE),),
    )
    schedule = Schedule(entries=_reference_entries())
    # Lock everything except r1 (forced to move) and r6 (the only slack
    # left for r1 to swap into).
    for rid, day_id in (("r2", "d1"), ("r3", "d2"), ("r4", "d2"), ("r5", "d3")):
        schedule = lock_occurrence(problem, schedule, rid, day_id, next(
            e.period_id for e in schedule.entries if e.requirement_id == rid and e.day_id == day_id
        ))

    result = reoptimize(problem, schedule)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    report = verify(problem, result.entries)
    assert report.passed, report.violations

    # Locked occurrences are exactly where they were.
    for rid, day_id, period_id in (
        ("r2", "d1", "q2"), ("r3", "d2", "q1"), ("r4", "d2", "q2"), ("r5", "d3", "q1"),
    ):
        matching = [e for e in result.entries if e.requirement_id == rid]
        assert len(matching) == 1
        assert (matching[0].day_id, matching[0].period_id) == (day_id, period_id)

    # r1 is no longer at its old (now-unavailable) slot.
    r1_slot = [(e.day_id, e.period_id) for e in result.entries if e.requirement_id == "r1"][0]
    assert r1_slot != ("d1", "q1")


# ---------------------------------------------------------------------------
# D. Impossible re-optimization -> INFEASIBLE (or INVALID_INPUT), never a
#    silently-unlocked schedule.
# ---------------------------------------------------------------------------

def test_lock_conflicting_with_new_hard_condition_is_infeasible():
    problem = _six_single_period_problem(
        teacher_availabilities=(TeacherAvailability("t1", "d1", "q1", AvailabilityStatus.UNAVAILABLE),),
    )
    schedule = Schedule(entries=_reference_entries())
    # Lock r1 to the exact slot that is now UNAVAILABLE for its teacher --
    # a direct contradiction (var forced both 0 and 1).
    schedule = lock_occurrence(problem, schedule, "r1", "d1", "q1")

    result = reoptimize(problem, schedule)
    assert result.status in (SolverStatus.INFEASIBLE, SolverStatus.INVALID_INPUT)
    assert result.entries == ()

    # The lock must never have been silently dropped to "fix" this --
    # confirmed by the fact that no schedule was returned at all.


# ---------------------------------------------------------------------------
# E. A previously valid reference can be repaired against each kind of
#    placement-feasibility rule the NEW problem may have legitimately
#    changed since the reference was generated -- none of these should
#    ever be rejected as an "invalid reference", only as a repair target.
# ---------------------------------------------------------------------------

def test_new_fixed_placement_conflicting_with_reference_is_repaired():
    problem = _six_single_period_problem()
    schedule = Schedule(entries=_reference_entries())

    # r1 currently sits at (d1, q1); r4 currently sits at (d2, q2). Force r1
    # to (d2, q2) instead -- directly conflicting with the reference.
    changed_problem = dataclasses.replace(
        problem, fixed_placements=(FixedPlacement("fp1", "r1", TimeSlot("d2", "q2")),),
    )

    result = reoptimize(changed_problem, schedule)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE), result.metadata
    report = verify(changed_problem, result.entries)
    assert report.passed, report.violations

    r1_slot = [(e.day_id, e.period_id) for e in result.entries if e.requirement_id == "r1"]
    assert r1_slot == [("d2", "q2")]
    assert schedule.entries == _reference_entries()  # reference untouched


def test_new_resource_capacity_conflicting_with_reference_is_repaired():
    gym = ResourceRequirement(resource_id="gym")
    teachers = (Teacher("t_a", "A"), Teacher("t_a2", "A2"), Teacher("t_b", "B"), Teacher("t_b2", "B2"))
    groups = (
        ParticipantGroup("pg_a", "A", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
        ParticipantGroup("pg_a2", "A2", ("cx",), ParticipantGroupRole.SUBGROUP),
        ParticipantGroup("pg_b", "B", ("cy",), ParticipantGroupRole.WHOLE_CLASS),
        ParticipantGroup("pg_b2", "B2", ("cy",), ParticipantGroupRole.SUBGROUP),
    )
    activities = (Activity("subj_a", "A"), Activity("subj_a2", "A2"), Activity("subj_b", "B"), Activity("subj_b2", "B2"))
    small_days = DAYS[:2]
    problem = SchedulingProblem(
        school=School(id="s", name="S"), academic_year=AcademicYear(id="ay", label="AY"),
        days=small_days, periods=PERIODS,
        teachers=teachers, class_sections=(ClassSection("cx", "CX"), ClassSection("cy", "CY")),
        participant_groups=groups, activities=activities,
        teaching_requirements=(
            TeachingRequirement("reqA", "t_a", "subj_a", "pg_a", 2, FLEXIBLE, resource_requirement=gym),
            TeachingRequirement("reqA2", "t_a2", "subj_a2", "pg_a2", 2, FLEXIBLE),
            TeachingRequirement("reqB", "t_b", "subj_b", "pg_b", 2, FLEXIBLE, resource_requirement=gym),
            TeachingRequirement("reqB2", "t_b2", "subj_b2", "pg_b2", 2, FLEXIBLE),
        ),
        resources=(Resource("gym", "Gym", capacity=2),),
        teacher_availabilities=(), reserved_blocks=(), fixed_placements=(),
    )
    def _two_class_entry(rid, act, day_id, period_id, teacher_id, group_id, class_id):
        return ScheduleEntry(
            source=EntrySource.REQUIREMENT, activity_id=act, day_id=day_id, period_id=period_id,
            class_sections=(class_id,), teacher_id=teacher_id, participant_group_id=group_id,
            requirement_id=rid,
        )

    entries = (
        _two_class_entry("reqA", "subj_a", "d1", "q1", "t_a", "pg_a", "cx"),
        _two_class_entry("reqA", "subj_a", "d1", "q2", "t_a", "pg_a", "cx"),
        _two_class_entry("reqA2", "subj_a2", "d2", "q1", "t_a2", "pg_a2", "cx"),
        _two_class_entry("reqA2", "subj_a2", "d2", "q2", "t_a2", "pg_a2", "cx"),
        _two_class_entry("reqB", "subj_b", "d1", "q1", "t_b", "pg_b", "cy"),
        _two_class_entry("reqB", "subj_b", "d2", "q1", "t_b", "pg_b", "cy"),
        _two_class_entry("reqB2", "subj_b2", "d1", "q2", "t_b2", "pg_b2", "cy"),
        _two_class_entry("reqB2", "subj_b2", "d2", "q2", "t_b2", "pg_b2", "cy"),
    )
    schedule = Schedule(entries=entries)
    before = schedule.entries

    # reqA and reqB both use the gym at (d1, q1) -- fine under capacity 2.
    report_before = verify(problem, schedule.entries)
    assert report_before.passed, report_before.violations

    changed_problem = dataclasses.replace(
        problem, resources=(Resource("gym", "Gym", capacity=1),),
    )

    result = reoptimize(changed_problem, schedule)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE), result.metadata
    report = verify(changed_problem, result.entries)
    assert report.passed, report.violations
    assert schedule.entries == before  # reference untouched


def test_new_reserved_block_conflicting_with_reference_is_repaired():
    # A 4th day is carved out entirely for a weekly club (2 reserved
    # slots, matching the club's own weekly_periods-like footprint) so the
    # closed system (6 lesson periods + 2 reserved slots == 8 total
    # instructional slots) never needs to change; only WHICH slots are
    # reserved does -- the same total footprint just moves.
    four_days = DAYS + (Day(id="d4", name="Day4", index=3),)
    teachers = tuple(Teacher(f"t{i}", f"T{i}") for i in range(1, 7))
    groups = tuple(
        ParticipantGroup(
            f"pg{i}", f"PG{i}", ("cx",),
            ParticipantGroupRole.WHOLE_CLASS if i == 1 else ParticipantGroupRole.SUBGROUP,
        )
        for i in range(1, 7)
    )
    activities = tuple(Activity(f"subj{i}", f"Subj{i}") for i in range(1, 7)) + (Activity("club_chess", "Chess"),)
    requirements = tuple(
        TeachingRequirement(f"r{i}", f"t{i}", f"subj{i}", f"pg{i}", 1, FLEXIBLE) for i in range(1, 7)
    )
    problem = SchedulingProblem(
        school=School(id="s", name="S"), academic_year=AcademicYear(id="ay", label="AY"),
        days=four_days, periods=PERIODS,
        teachers=teachers, class_sections=(ClassSection("cx", "CX"),),
        participant_groups=groups, activities=activities, teaching_requirements=requirements,
        resources=(), teacher_availabilities=(),
        reserved_blocks=(
            ReservedBlock("club1", "Chess Club", "club_chess", ("cx",), (
                TimeSlot("d4", "q1"), TimeSlot("d4", "q2"),
            )),
        ),
        fixed_placements=(),
    )
    entries = (
        _entry("r1", "subj1", "d1", "q1", "t1", "pg1"),
        _entry("r2", "subj2", "d1", "q2", "t2", "pg2"),
        _entry("r3", "subj3", "d2", "q1", "t3", "pg3"),
        _entry("r4", "subj4", "d2", "q2", "t4", "pg4"),
        _entry("r5", "subj5", "d3", "q1", "t5", "pg5"),
        _entry("r6", "subj6", "d3", "q2", "t6", "pg6"),
        ScheduleEntry(
            source=EntrySource.RESERVED_BLOCK, activity_id="club_chess", day_id="d4", period_id="q1",
            class_sections=("cx",), reserved_block_id="club1",
        ),
        ScheduleEntry(
            source=EntrySource.RESERVED_BLOCK, activity_id="club_chess", day_id="d4", period_id="q2",
            class_sections=("cx",), reserved_block_id="club1",
        ),
    )
    schedule = Schedule(entries=entries)
    before = schedule.entries
    report_before = verify(problem, schedule.entries)
    assert report_before.passed, report_before.violations

    # Move ONE of the club's two slots from (d4, q1) to (d1, q1) --
    # directly conflicting with r1's current ordinary lesson placement
    # there. Total reserved footprint stays 2 slots, so no requirement's
    # weekly_periods needs to change for the system to stay balanced.
    changed_problem = dataclasses.replace(
        problem,
        reserved_blocks=(
            ReservedBlock("club1", "Chess Club", "club_chess", ("cx",), (
                TimeSlot("d1", "q1"), TimeSlot("d4", "q2"),
            )),
        ),
    )

    result = reoptimize(changed_problem, schedule)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE), result.metadata
    report = verify(changed_problem, result.entries)
    assert report.passed, report.violations
    assert schedule.entries == before  # reference untouched


def test_tightened_max_periods_per_day_conflicting_with_reference_is_repaired():
    small_days = DAYS[:2]
    problem = SchedulingProblem(
        school=School(id="s", name="S"), academic_year=AcademicYear(id="ay", label="AY"),
        days=small_days, periods=PERIODS,
        teachers=(Teacher("t1", "T1"), Teacher("t2", "T2")),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(
            ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP),
        ),
        activities=(Activity("subj1", "Subj1"), Activity("subj2", "Subj2")),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "subj1", "pg1", 2, FLEXIBLE),
            TeachingRequirement("r2", "t2", "subj2", "pg2", 2, FLEXIBLE),
        ),
        resources=(), teacher_availabilities=(), reserved_blocks=(), fixed_placements=(),
    )
    entries = (
        _entry("r1", "subj1", "d1", "q1", "t1", "pg1"),
        _entry("r1", "subj1", "d1", "q2", "t1", "pg1"),
        _entry("r2", "subj2", "d2", "q1", "t2", "pg2"),
        _entry("r2", "subj2", "d2", "q2", "t2", "pg2"),
    )
    schedule = Schedule(entries=entries)
    before = schedule.entries
    report_before = verify(problem, schedule.entries)
    assert report_before.passed, report_before.violations

    # r1 currently has BOTH its periods on d1 -- fine with no cap. Tighten
    # the cap to 1/day, directly conflicting with the reference.
    changed_r1 = dataclasses.replace(
        problem.teaching_requirements[0], distribution_policy=DistributionPolicy(max_periods_per_day=1),
    )
    changed_problem = dataclasses.replace(
        problem, teaching_requirements=(changed_r1, problem.teaching_requirements[1]),
    )

    result = reoptimize(changed_problem, schedule)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE), result.metadata
    report = verify(changed_problem, result.entries)
    assert report.passed, report.violations
    assert schedule.entries == before  # reference untouched

    r1_days = {e.day_id for e in result.entries if e.requirement_id == "r1"}
    assert len(r1_days) == 2  # r1's two periods are now on different days
