"""Phase 2A: generic REQUIRED lesson-block pattern coverage.

Each end-to-end test uses a small, purpose-built calendar (not the large
Phase-1 fixture) so the pattern under test is easy to reason about: one
"core" requirement carries the REQUIRED pattern, and a FLEXIBLE "filler"
requirement (a different teacher) tops up the same class to exactly full
occupancy, since that is a Phase-1 HARD constraint this slice must not
weaken.
"""
from __future__ import annotations

from collections import defaultdict

from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear, Day, Period
from school_timetable.domain.groups import ClassSection, ParticipantGroup
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    DistributionPolicy,
    LessonBlockPolicy,
    TeachingRequirement,
)
from school_timetable.domain.result import EntrySource, ScheduleEntry, SolverStatus
from school_timetable.domain.school import School
from school_timetable.scheduling.solver import solve
from school_timetable.validation.preflight import run_preflight
from school_timetable.verification.verifier import verify

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)


def _days(n: int) -> tuple[Day, ...]:
    return tuple(Day(id=f"d{i}", name=f"Day {i}", index=i) for i in range(n))


def _single_block_periods(n: int) -> tuple[Period, ...]:
    """`n` periods, all one uninterrupted structural block (no break)."""
    return tuple(Period(id=f"p{i}", name=f"P{i}", index=i, block_id="all") for i in range(n))


def _two_block_periods(sizes: list[int]) -> tuple[Period, ...]:
    """Periods split into separate structural blocks of the given sizes,
    e.g. [2, 2] -> a 2-period morning block and a 2-period afternoon
    block, with a break between them (mirrors the Phase-1 lunch break)."""
    periods = []
    idx = 0
    for block_num, size in enumerate(sizes):
        for _ in range(size):
            periods.append(Period(id=f"p{idx}", name=f"P{idx}", index=idx, block_id=f"blk{block_num}"))
            idx += 1
    return tuple(periods)


def _problem_with_pattern(
    days: tuple[Day, ...],
    periods: tuple[Period, ...],
    pattern: tuple[int, ...],
    weekly_periods: int,
    max_periods_per_day: int | None = None,
) -> SchedulingProblem:
    total_slots = len(days) * len(periods)
    filler_periods = total_slots - weekly_periods
    assert filler_periods >= 0, "test setup error: pattern exceeds total slots"

    return SchedulingProblem(
        school=School(id="s", name="S"),
        academic_year=AcademicYear(id="ay", label="AY"),
        days=days,
        periods=periods,
        teachers=(Teacher(id="t_core", name="Core"), Teacher(id="t_filler", name="Filler")),
        class_sections=(ClassSection(id="c1", name="C1"),),
        participant_groups=(ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",)),),
        activities=(Activity(id="core", name="Core"), Activity(id="filler", name="Filler")),
        teaching_requirements=(
            TeachingRequirement(
                id="core", teacher_id="t_core", activity_id="core", participant_group_id="pg1",
                weekly_periods=weekly_periods,
                block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=pattern),
                distribution_policy=DistributionPolicy(max_periods_per_day=max_periods_per_day)
                if max_periods_per_day is not None else DistributionPolicy(),
            ),
            TeachingRequirement(
                id="filler", teacher_id="t_filler", activity_id="filler", participant_group_id="pg1",
                weekly_periods=filler_periods, block_policy=FLEXIBLE,
            ),
        ) if filler_periods > 0 else (
            TeachingRequirement(
                id="core", teacher_id="t_core", activity_id="core", participant_group_id="pg1",
                weekly_periods=weekly_periods,
                block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=pattern),
                distribution_policy=DistributionPolicy(max_periods_per_day=max_periods_per_day)
                if max_periods_per_day is not None else DistributionPolicy(),
            ),
        ),
    )


def _core_periods_by_day(entries) -> dict[str, list[str]]:
    by_day: dict[str, list[str]] = defaultdict(list)
    for e in entries:
        if e.requirement_id == "core":
            by_day[e.day_id].append(e.period_id)
    return by_day


# -- End-to-end solves for representative generic patterns -----------------

def test_required_pattern_two_two_solves_and_matches_pattern():
    problem = _problem_with_pattern(
        days=_days(3), periods=_single_block_periods(4), pattern=(2, 2), weekly_periods=4,
    )
    assert run_preflight(problem) == []

    result = solve(problem)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

    report = verify(problem, result.entries)
    assert report.passed, report.violations

    by_day = _core_periods_by_day(result.entries)
    lengths = sorted(len(v) for v in by_day.values() if v)
    assert lengths == [2, 2]


def test_required_pattern_two_one_one_one_solves_and_matches_pattern():
    problem = _problem_with_pattern(
        days=_days(5), periods=_single_block_periods(4), pattern=(2, 1, 1, 1), weekly_periods=5,
    )
    assert run_preflight(problem) == []

    result = solve(problem)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

    report = verify(problem, result.entries)
    assert report.passed, report.violations

    by_day = _core_periods_by_day(result.entries)
    lengths = sorted(len(v) for v in by_day.values() if v)
    assert lengths == [1, 1, 1, 2]


def test_required_pattern_three_one_solves_and_matches_pattern():
    problem = _problem_with_pattern(
        days=_days(3), periods=_single_block_periods(4), pattern=(3, 1), weekly_periods=4,
    )
    assert run_preflight(problem) == []

    result = solve(problem)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

    report = verify(problem, result.entries)
    assert report.passed, report.violations

    by_day = _core_periods_by_day(result.entries)
    lengths = sorted(len(v) for v in by_day.values() if v)
    assert lengths == [1, 3]

    # The length-3 day must be one genuinely consecutive run.
    triple_day = [day for day, pids in by_day.items() if len(pids) == 3][0]
    periods_by_id = {p.id: p for p in problem.periods}
    ordered = sorted((periods_by_id[pid] for pid in by_day[triple_day]), key=lambda p: p.index)
    assert [p.index for p in ordered] == [ordered[0].index + i for i in range(3)]
    assert len({p.block_id for p in ordered}) == 1


# -- Boundary / placeability rules -----------------------------------------

def test_required_block_cannot_cross_structural_break_preflight_rejects():
    # Two separate 2-period blocks (a break in between) -> no run is long
    # enough to host a length-3 block.
    problem = _problem_with_pattern(
        days=_days(3), periods=_two_block_periods([2, 2]), pattern=(3, 1), weekly_periods=4,
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "BLOCK_LENGTH_UNPLACEABLE" in codes


def test_required_block_crossing_break_is_flagged_by_verifier():
    """Adversarial: if a (hypothetically buggy) solver produced a 'block'
    whose two periods sit in different structural blocks, the independent
    verifier must catch it -- this never relies on CP-SAT's own domain
    restrictions, since it inspects raw entries only."""
    problem = _problem_with_pattern(
        days=_days(2), periods=_two_block_periods([2, 2]), pattern=(2, 2), weekly_periods=4,
    )
    periods = problem.periods  # p0,p1 in blk0; p2,p3 in blk1
    entries = (
        # Day d0: a "block" straddling the break (p1 in blk0, p2 in blk1).
        ScheduleEntry(source=EntrySource.REQUIREMENT, activity_id="core", day_id="d0", period_id="p1",
                      class_sections=("c1",), teacher_id="t_core", participant_group_id="pg1",
                      requirement_id="core"),
        ScheduleEntry(source=EntrySource.REQUIREMENT, activity_id="core", day_id="d0", period_id="p2",
                      class_sections=("c1",), teacher_id="t_core", participant_group_id="pg1",
                      requirement_id="core"),
        # Day d1: a genuinely valid block, to keep the pattern's per-day
        # length multiset matching (2, 2) so only the boundary-crossing
        # defect is being isolated.
        ScheduleEntry(source=EntrySource.REQUIREMENT, activity_id="core", day_id="d1", period_id="p0",
                      class_sections=("c1",), teacher_id="t_core", participant_group_id="pg1",
                      requirement_id="core"),
        ScheduleEntry(source=EntrySource.REQUIREMENT, activity_id="core", day_id="d1", period_id="p1",
                      class_sections=("c1",), teacher_id="t_core", participant_group_id="pg1",
                      requirement_id="core"),
    )
    report = verify(problem, entries)
    assert not report.passed
    assert any("not a single consecutive same-block_id run" in v for v in report.violations)


# -- Preflight rejection rules ----------------------------------------------

def test_required_pattern_sum_mismatch_rejected_by_preflight():
    problem = _problem_with_pattern(
        days=_days(3), periods=_single_block_periods(4), pattern=(2, 2), weekly_periods=5,
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "BLOCK_PATTERN_TOTAL_MISMATCH" in codes


def test_required_zero_block_length_rejected():
    problem = _problem_with_pattern(
        days=_days(3), periods=_single_block_periods(4), pattern=(0, 4), weekly_periods=4,
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "NON_POSITIVE_BLOCK_LENGTH" in codes


def test_required_negative_block_length_rejected():
    problem = _problem_with_pattern(
        days=_days(3), periods=_single_block_periods(4), pattern=(-1, 5), weekly_periods=4,
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "NON_POSITIVE_BLOCK_LENGTH" in codes


def test_required_too_many_blocks_for_available_days_rejected():
    # 3 pattern elements, only 2 school days configured.
    problem = _problem_with_pattern(
        days=_days(2), periods=_single_block_periods(4), pattern=(1, 1, 1), weekly_periods=3,
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "TOO_MANY_BLOCKS_FOR_AVAILABLE_DAYS" in codes


def test_required_max_periods_per_day_smaller_than_largest_block_rejected():
    problem = _problem_with_pattern(
        days=_days(3), periods=_single_block_periods(4), pattern=(3, 1), weekly_periods=4,
        max_periods_per_day=2,
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "BLOCK_EXCEEDS_MAX_PERIODS_PER_DAY" in codes


def test_required_block_length_that_fits_no_run_rejected():
    # Two 1-period "blocks" (each is its own tiny structural run of size
    # 1): no run anywhere is long enough for a length-2 block.
    problem = _problem_with_pattern(
        days=_days(3), periods=_two_block_periods([1, 1]), pattern=(2,), weekly_periods=2,
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "BLOCK_LENGTH_UNPLACEABLE" in codes


# -- Verifier: malformed multi-block schedule -------------------------------

def test_verifier_detects_malformed_required_multi_block_schedule():
    """Adversarial: entries whose per-day length multiset does not match
    the declared (2, 2) pattern at all (e.g. four separate singles)."""
    problem = _problem_with_pattern(
        days=_days(4), periods=_single_block_periods(4), pattern=(2, 2), weekly_periods=4,
    )
    entries = tuple(
        ScheduleEntry(source=EntrySource.REQUIREMENT, activity_id="core", day_id=f"d{i}", period_id="p0",
                      class_sections=("c1",), teacher_id="t_core", participant_group_id="pg1",
                      requirement_id="core")
        for i in range(4)
    )
    report = verify(problem, entries)
    assert not report.passed
    assert any("does not match actual" in v for v in report.violations)
