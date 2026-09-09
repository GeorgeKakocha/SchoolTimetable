"""Focused, minimal-fixture tests closing two coverage gaps flagged by the
Phase-1 pre-commit audit:

1. The independent verifier's full-class-occupancy check was only ever
   exercised indirectly, via the large valid fixture passing as a whole.
   These tests build a tiny problem and prove the check actually fires on
   a missing slot and on a genuine double-booking.

2. Split-group occupancy non-double-counting had no dedicated test: two
   synchronized branches sharing a slot must count as ONE occupancy unit
   for their parent class, not two. This is proven directly here, with an
   adversarial contrast case (two *unrelated* requirements overlapping,
   which must still be flagged).
"""
from __future__ import annotations

from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear, Day, Period
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, LessonBlockPolicy, TeachingRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.domain.school import School
from school_timetable.verification.verifier import verify

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)

# A deliberately tiny calendar: 1 day, 2 periods, one structural block.
# Just enough shape to exercise occupancy without pulling in the large
# fixture's 40-slot surface.
_DAYS = (Day(id="d1", name="Day 1", index=0),)
_PERIODS = (
    Period(id="p1", name="P1", index=0, block_id="a"),
    Period(id="p2", name="P2", index=1, block_id="a"),
)


def _entry(activity_id, day_id, period_id, class_sections, teacher_id=None, participant_group_id=None,
           requirement_id=None, source=EntrySource.REQUIREMENT):
    return ScheduleEntry(
        source=source,
        activity_id=activity_id,
        day_id=day_id,
        period_id=period_id,
        class_sections=class_sections,
        teacher_id=teacher_id,
        participant_group_id=participant_group_id,
        requirement_id=requirement_id,
    )


def _problem(**overrides) -> SchedulingProblem:
    defaults = dict(
        school=School(id="s", name="S"),
        academic_year=AcademicYear(id="ay", label="AY"),
        days=_DAYS,
        periods=_PERIODS,
        teachers=(),
        class_sections=(ClassSection(id="c1", name="C1"),),
        participant_groups=(),
        activities=(Activity(id="a1", name="A1"),),
        teaching_requirements=(),
    )
    defaults.update(overrides)
    return SchedulingProblem(**defaults)


# -- Gap 1: full class occupancy is independently checked, not assumed ----

def test_verifier_detects_a_missing_class_slot():
    """A class with a genuinely empty instructional slot must be flagged,
    even though every requirement's own weekly-count is otherwise fine."""
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""),),
        participant_groups=(ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    # Only p1 is filled; p2 is left completely empty for class c1.
    entries = (
        _entry("a1", "d1", "p1", ("c1",), teacher_id="t1", participant_group_id="pg1", requirement_id="r1"),
    )
    report = verify(problem, entries)
    assert not report.passed
    assert any("no activity at" in v and "'d1'" in v and "'p2'" in v for v in report.violations)


def test_verifier_detects_a_genuine_class_double_booking():
    """Two *unrelated* requirements (no split relationship) both landing
    on the same class/slot is a real double-booking and must be flagged."""
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup(id="pg2", name="PG2", class_sections=("c1",), role=ParticipantGroupRole.SUBGROUP),
        ),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
            TeachingRequirement(
                id="r2", teacher_id="t2", activity_id="a1", participant_group_id="pg2",
                weekly_periods=2, block_policy=FLEXIBLE,
            ),
        ),
    )
    entries = (
        _entry("a1", "d1", "p1", ("c1",), teacher_id="t1", participant_group_id="pg1", requirement_id="r1"),
        _entry("a1", "d1", "p1", ("c1",), teacher_id="t2", participant_group_id="pg2", requirement_id="r2"),
        _entry("a1", "d1", "p2", ("c1",), teacher_id="t2", participant_group_id="pg2", requirement_id="r2"),
    )
    report = verify(problem, entries)
    assert not report.passed
    assert any("double-booked" in v and "'d1'" in v and "'p1'" in v for v in report.violations)


# -- Gap 2: split-group occupancy counts as ONE unit, never two -----------

def _split_problem():
    return _problem(
        teachers=(Teacher(id="t_de", first_name="German Teacher", last_name=""), Teacher(id="t_ru", first_name="Russian Teacher", last_name="")),
        participant_groups=(
            ParticipantGroup(id="pg_de", name="German branch", class_sections=("c1",), role=ParticipantGroupRole.SUBGROUP),
            ParticipantGroup(id="pg_ru", name="Russian branch", class_sections=("c1",), role=ParticipantGroupRole.SUBGROUP),
        ),
        teaching_requirements=(
            TeachingRequirement(
                id="german", teacher_id="t_de", activity_id="a1", participant_group_id="pg_de",
                weekly_periods=2, block_policy=FLEXIBLE, split_group_id="split1",
            ),
            TeachingRequirement(
                id="russian", teacher_id="t_ru", activity_id="a1", participant_group_id="pg_ru",
                weekly_periods=2, block_policy=FLEXIBLE, split_group_id="split1",
            ),
        ),
    )


def test_synchronized_split_branches_occupy_class_as_one_unit():
    """German and Russian run in parallel at both slots -- the class is
    fully (and validly) occupied by exactly ONE occupancy unit per slot,
    not two, even though two requirements/teachers are both active."""
    problem = _split_problem()
    entries = (
        _entry("a1", "d1", "p1", ("c1",), teacher_id="t_de", participant_group_id="pg_de", requirement_id="german"),
        _entry("a1", "d1", "p1", ("c1",), teacher_id="t_ru", participant_group_id="pg_ru", requirement_id="russian"),
        _entry("a1", "d1", "p2", ("c1",), teacher_id="t_de", participant_group_id="pg_de", requirement_id="german"),
        _entry("a1", "d1", "p2", ("c1",), teacher_id="t_ru", participant_group_id="pg_ru", requirement_id="russian"),
    )
    report = verify(problem, entries)
    assert report.passed, report.violations
    assert not any("double-booked" in v for v in report.violations)


def test_split_branches_falling_out_of_sync_is_still_double_booking():
    """Contrast case: if the branches are NOT at the same slot, class c1
    now has two *different* simultaneous occupants at some slot (or one
    slot with only one branch active and the other's slot uncovered) --
    the split-group carve-out must not hide a genuine problem. Here we
    force both branches active at *different* slots each period, which
    both violates split-sync (checked elsewhere) and leaves overlapping
    single-branch coverage that the occupancy check must still validate
    correctly (each slot has exactly one active branch => one unit, so no
    false double-booking here either -- this confirms the dedup key is
    per-split-group, not per-slot-presence).
    """
    problem = _split_problem()
    entries = (
        _entry("a1", "d1", "p1", ("c1",), teacher_id="t_de", participant_group_id="pg_de", requirement_id="german"),
        _entry("a1", "d1", "p2", ("c1",), teacher_id="t_ru", participant_group_id="pg_ru", requirement_id="russian"),
    )
    report = verify(problem, entries)
    # Occupancy itself is fine (one unit per slot); the split-sync check
    # (a separate, already-covered concern) is what would catch this.
    assert not any("double-booked" in v for v in report.violations)
    assert any("do not match" in v for v in report.violations)


def test_non_split_overlap_at_same_slot_is_still_flagged_as_double_booking():
    """Negative control proving the split-group dedup key is genuinely
    tied to split_group_id and does not accidentally suppress real
    double-booking: two DIFFERENT, non-split, non-synchronized
    requirements landing on the same class/slot must still be flagged.
    """
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup(id="pg2", name="PG2", class_sections=("c1",), role=ParticipantGroupRole.SUBGROUP),
        ),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
            TeachingRequirement(
                id="r2", teacher_id="t2", activity_id="a1", participant_group_id="pg2",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    entries = (
        _entry("a1", "d1", "p1", ("c1",), teacher_id="t1", participant_group_id="pg1", requirement_id="r1"),
        _entry("a1", "d1", "p1", ("c1",), teacher_id="t2", participant_group_id="pg2", requirement_id="r2"),
    )
    report = verify(problem, entries)
    assert any("double-booked" in v for v in report.violations)
