"""Phase 2C pre-commit audit fixes: stale-plan safety and invalid-input
defense, reproducing the exact scenarios the audit found.

Covers audit items A-F:
  A. Stale MovePlan (unrelated change touches the swap partner itself).
  B. Stale MovePlan (unrelated change introduces a fresh teacher conflict).
  C. Normal validate -> apply still succeeds on an unchanged schedule.
  D. A malformed (non-consecutive REQUIRED block) input Schedule is
     rejected by validate_move with INVALID_SCHEDULE, no plan produced.
  E. find_logical_occurrence itself raises EditingError(code="INVALID_SCHEDULE")
     on the same malformed block, independent of validate_move.
  F. reoptimize rejects a malformed reference Schedule with INVALID_INPUT,
     never falling back to an unlocked solve, never mutating the reference.
"""
from __future__ import annotations

from editing_test_support import fill_occupancy

from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear, Day, Period
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, LessonBlockPolicy, TeachingRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry, SolverStatus
from school_timetable.domain.schedule import Schedule
from school_timetable.domain.school import School
from school_timetable.scheduling.editing import EditingError, apply_move, find_logical_occurrence, validate_move
from school_timetable.scheduling.reoptimize import reoptimize
from school_timetable.verification.verifier import verify

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)
REQUIRED_2 = LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2,))

DAYS = tuple(Day(id=f"d{i}", name=f"Day{i}", index=i - 1) for i in range(1, 3))
PERIODS = (
    Period(id="q1", name="Q1", index=0, block_id="am"),
    Period(id="q2", name="Q2", index=1, block_id="am"),
    Period(id="q3", name="Q3", index=2, block_id="pm"),
    Period(id="q4", name="Q4", index=3, block_id="pm"),
)


def _entry(requirement_id, activity_id, day_id, period_id, class_sections, teacher_id, group_id):
    return ScheduleEntry(
        source=EntrySource.REQUIREMENT, activity_id=activity_id, day_id=day_id, period_id=period_id,
        class_sections=class_sections, teacher_id=teacher_id, participant_group_id=group_id,
        requirement_id=requirement_id,
    )


def _problem(**overrides) -> SchedulingProblem:
    defaults = dict(
        school=School(id="s", name="S"), academic_year=AcademicYear(id="ay", label="AY"),
        days=DAYS, periods=PERIODS,
        teachers=(), class_sections=(), participant_groups=(), activities=(),
        teaching_requirements=(), resources=(), teacher_availabilities=(),
        reserved_blocks=(), fixed_placements=(),
    )
    defaults.update(overrides)
    return SchedulingProblem(**defaults)


def _full(problem, entries):
    filled_problem, filled_entries = fill_occupancy(problem, entries)
    return filled_problem, Schedule(entries=filled_entries)


# ---------------------------------------------------------------------------
# A. Stale MovePlan: the unrelated change moves the ORIGINAL SWAP PARTNER
#    itself away, so re-resolving the same intent finds a different
#    occupant -- the plan must be rejected rather than silently applied
#    against entries that no longer mean what they meant when validated.
# ---------------------------------------------------------------------------

def _two_class_problem():
    return _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name=""), Teacher(id="t3", first_name="T3", last_name="")),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(
            ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP),
            ParticipantGroup("pg3", "PG3", ("cx",), ParticipantGroupRole.SUBGROUP),
        ),
        activities=(Activity("math", "Math"), Activity("art", "Art"), Activity("music", "Music")),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE),
            TeachingRequirement("r2", "t2", "art", "pg2", 1, FLEXIBLE),
            TeachingRequirement("r3", "t3", "music", "pg3", 1, FLEXIBLE),
        ),
    )


def test_stale_plan_rejected_when_swap_partner_moves_away():
    problem = _two_class_problem()
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d1", "q2", ("cx",), "t2", "pg2"),
        _entry("r3", "music", "d2", "q1", ("cx",), "t3", "pg3"),
    )
    problem, schedule_a = _full(problem, entries)

    # Validate against A: move r1 (d1/q1) to swap with r3 (d2/q1).
    stale_result = validate_move(problem, schedule_a, "r1", "d1", "q1", "d2", "q1")
    assert stale_result.allowed, stale_result.violations
    assert {e.requirement_id for e in stale_result.plan.removed_entries} == {"r1", "r3"}

    # A separate, entirely legitimate edit: swap r3 (the ORIGINAL plan's
    # target occupant) with r2 -- valid on its own, produces schedule B.
    unrelated = validate_move(problem, schedule_a, "r3", "d2", "q1", "d1", "q2")
    assert unrelated.allowed, unrelated.violations
    schedule_b = apply_move(problem, schedule_a, unrelated)
    assert schedule_b.entries != schedule_a.entries

    # Applying the stale plan (still referring to the original MoveIntent)
    # to B must be rejected -- r3 is no longer at (d2, q1), r2 is.
    before = schedule_b.entries
    try:
        apply_move(problem, schedule_b, stale_result)
        assert False, "expected apply_move to reject the stale plan"
    except EditingError as exc:
        assert exc.code == "STALE_MOVE_PLAN"

    # B is completely untouched by the rejected attempt.
    assert schedule_b.entries == before
    report = verify(problem, schedule_b.entries)
    assert report.passed, report.violations


# ---------------------------------------------------------------------------
# B. Stale MovePlan: the unrelated change leaves the same occupant in
#    place but introduces a fresh teacher conflict at the move's target
#    slot (via a different class using the same teacher) -- fresh
#    revalidation must catch this and reject rather than silently apply a
#    now-HARD-invalid swap.
# ---------------------------------------------------------------------------

def test_stale_plan_rejected_when_unrelated_change_causes_teacher_conflict():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name=""), Teacher(id="t7", first_name="T7", last_name="")),
        class_sections=(ClassSection("cx", "CX"), ClassSection("cy", "CY")),
        participant_groups=(
            ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP),
            ParticipantGroup("pg6", "PG6", ("cy",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg7", "PG7", ("cy",), ParticipantGroupRole.SUBGROUP),
        ),
        activities=(
            Activity("math", "Math"), Activity("art", "Art"),
            Activity("german2", "German2"), Activity("history", "History"),
        ),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE),
            TeachingRequirement("r2", "t2", "art", "pg2", 1, FLEXIBLE),
            # cy's own pair, used for the unrelated move: r6 (taught by t1,
            # the SAME teacher as r1) currently sits away from (d2, q1).
            TeachingRequirement("r6", "t1", "german2", "pg6", 1, FLEXIBLE),
            TeachingRequirement("r7", "t7", "history", "pg7", 1, FLEXIBLE),
        ),
    )
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d2", "q1", ("cx",), "t2", "pg2"),
        _entry("r6", "german2", "d1", "q2", ("cy",), "t1", "pg6"),
        _entry("r7", "history", "d2", "q1", ("cy",), "t7", "pg7"),
    )
    problem, schedule_a = _full(problem, entries)

    # Validate against A: move r1 to (d2, q1), swapping with r2. Valid --
    # t1 (r1's teacher) is free at (d2, q1) at this point.
    stale_result = validate_move(problem, schedule_a, "r1", "d1", "q1", "d2", "q1")
    assert stale_result.allowed, stale_result.violations

    # Unrelated, legitimate edit on cy: move r6 (taught by t1) into
    # (d2, q1), swapping with r7. This never touches r1 or r2 directly.
    unrelated = validate_move(problem, schedule_a, "r6", "d1", "q2", "d2", "q1")
    assert unrelated.allowed, unrelated.violations
    schedule_b = apply_move(problem, schedule_a, unrelated)

    # Now t1 already teaches cy's r6 at (d2, q1) -- re-applying the
    # original r1 move would double-book t1 there. Must be rejected.
    before = schedule_b.entries
    try:
        apply_move(problem, schedule_b, stale_result)
        assert False, "expected apply_move to reject the now-conflicting stale plan"
    except EditingError as exc:
        assert exc.code == "STALE_MOVE_PLAN"

    assert schedule_b.entries == before
    report = verify(problem, schedule_b.entries)
    assert report.passed, report.violations


# ---------------------------------------------------------------------------
# C. Normal path: validate -> apply on a genuinely unchanged schedule
#    still succeeds, and the result verifies.
# ---------------------------------------------------------------------------

def test_normal_validate_then_apply_still_succeeds_on_unchanged_schedule():
    problem = _two_class_problem()
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d1", "q2", ("cx",), "t2", "pg2"),
        _entry("r3", "music", "d2", "q1", ("cx",), "t3", "pg3"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d2", "q1")
    assert result.allowed, result.violations

    new_schedule = apply_move(problem, schedule, result)
    r1_slot = [(e.day_id, e.period_id) for e in new_schedule.entries if e.requirement_id == "r1"]
    assert r1_slot == [("d2", "q1")]
    report = verify(problem, new_schedule.entries)
    assert report.passed, report.violations


# ---------------------------------------------------------------------------
# D & E. Malformed REQUIRED block input (non-consecutive periods q1, q3 --
#    crossing the am/pm structural break) must never be trusted.
# ---------------------------------------------------------------------------

def _malformed_required_problem_and_schedule():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""),),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),),
        activities=(Activity("math", "Math"),),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pg1", 2, REQUIRED_2),
        ),
    )
    # Malformed on purpose: q1 and q3 are not adjacent (q2 separates them)
    # and cross the am/pm structural break -- never a real REQUIRED block.
    schedule = Schedule(entries=(
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r1", "math", "d1", "q3", ("cx",), "t1", "pg1"),
    ))
    return problem, schedule


def test_malformed_required_block_input_rejected_by_validate_move():
    problem, schedule = _malformed_required_problem_and_schedule()

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d1", "q1")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "INVALID_SCHEDULE" in codes
    assert result.plan is None


def test_malformed_required_block_raises_in_find_logical_occurrence_directly():
    problem, schedule = _malformed_required_problem_and_schedule()
    index = ProblemIndex(problem)

    try:
        find_logical_occurrence(problem, index, schedule, "r1", "d1", "q1")
        assert False, "expected EditingError for a non-consecutive REQUIRED block"
    except EditingError as exc:
        assert exc.code == "INVALID_SCHEDULE"


# ---------------------------------------------------------------------------
# F. reoptimize must reject a malformed reference Schedule outright, never
#    falling back to an unlocked solve, and never mutating the reference.
# ---------------------------------------------------------------------------

def test_reoptimize_rejects_malformed_reference_schedule():
    problem, schedule = _malformed_required_problem_and_schedule()
    before = schedule.entries

    result = reoptimize(problem, schedule)
    assert result.status == SolverStatus.INVALID_INPUT
    assert result.entries == ()
    assert schedule.entries == before
