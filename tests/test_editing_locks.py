"""Phase 2C: locking / unlocking logical occurrences."""
from __future__ import annotations

from school_timetable.domain.activities import Activity
from school_timetable.domain.blocks import FixedPlacement
from school_timetable.domain.calendar import AcademicYear, Day, Period, TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, LessonBlockPolicy, TeachingRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.domain.schedule import OccurrenceKey, Schedule
from school_timetable.domain.school import School
from school_timetable.scheduling.editing import lock_occurrence, unlock_occurrence

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)
DAYS = tuple(Day(id=f"d{i}", name=f"Day{i}", index=i - 1) for i in range(1, 4))
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


def test_lock_ordinary_occurrence():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""),), class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),), activities=(Activity("math", "Math"),),
        teaching_requirements=(TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE),),
    )
    schedule = Schedule(entries=(_entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),))

    locked = lock_occurrence(problem, schedule, "r1", "d1", "q1")
    assert locked.is_locked(OccurrenceKey("r1", "d1", "q1"))
    assert not schedule.is_locked(OccurrenceKey("r1", "d1", "q1"))  # original unchanged (immutable)


def test_unlock_occurrence():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""),), class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),), activities=(Activity("math", "Math"),),
        teaching_requirements=(TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE),),
    )
    schedule = Schedule(entries=(_entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),))

    locked = lock_occurrence(problem, schedule, "r1", "d1", "q1")
    unlocked = unlock_occurrence(problem, locked, "r1", "d1", "q1")
    assert not unlocked.is_locked(OccurrenceKey("r1", "d1", "q1"))


def test_lock_split_occurrence_keeps_all_branches_coherent():
    problem = _problem(
        teachers=(Teacher(id="t_de", first_name="DE", last_name=""), Teacher(id="t_ru", first_name="RU", last_name="")), class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(
            ParticipantGroup("pg_de", "DE", ("cx",), ParticipantGroupRole.SUBGROUP),
            ParticipantGroup("pg_ru", "RU", ("cx",), ParticipantGroupRole.SUBGROUP),
        ),
        activities=(Activity("german", "German"), Activity("russian", "Russian")),
        teaching_requirements=(
            TeachingRequirement("german", "t_de", "german", "pg_de", 1, FLEXIBLE, split_group_id="s1"),
            TeachingRequirement("russian", "t_ru", "russian", "pg_ru", 1, FLEXIBLE, split_group_id="s1"),
        ),
    )
    schedule = Schedule(entries=(
        _entry("german", "german", "d1", "q1", ("cx",), "t_de", "pg_de"),
        _entry("russian", "russian", "d1", "q1", ("cx",), "t_ru", "pg_ru"),
    ))

    locked = lock_occurrence(problem, schedule, "german", "d1", "q1")
    assert locked.is_locked(OccurrenceKey("german", "d1", "q1"))
    assert locked.is_locked(OccurrenceKey("russian", "d1", "q1"))  # sibling locked too, automatically

    unlocked = unlock_occurrence(problem, locked, "russian", "d1", "q1")  # unlock via either branch
    assert not unlocked.is_locked(OccurrenceKey("german", "d1", "q1"))
    assert not unlocked.is_locked(OccurrenceKey("russian", "d1", "q1"))


def test_lock_multi_period_required_block_keeps_whole_block_coherent():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""),), class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),), activities=(Activity("math", "Math"),),
        teaching_requirements=(
            TeachingRequirement(
                "r1", "t1", "math", "pg1", 2, LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2,)),
            ),
        ),
    )
    schedule = Schedule(entries=(
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r1", "math", "d1", "q2", ("cx",), "t1", "pg1"),
    ))

    # Locking via either member period locks the whole block under the
    # SAME anchor (lowest-index period).
    locked_via_q1 = lock_occurrence(problem, schedule, "r1", "d1", "q1")
    locked_via_q2 = lock_occurrence(problem, schedule, "r1", "d1", "q2")
    assert locked_via_q1.locked_occurrences == locked_via_q2.locked_occurrences
    assert locked_via_q1.is_locked(OccurrenceKey("r1", "d1", "q1"))


def _small_full_occupancy_problem(**overrides):
    """2 days x 2 periods = 4 slots, exactly matching r1(2) + r2(2) so
    preflight's full-class-occupancy check passes -- reoptimize requires
    a genuinely valid (fully occupied) problem, unlike the lighter
    lock/unlock tests above."""
    small_days = DAYS[:2]
    small_periods = PERIODS[:2]
    defaults = dict(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")), class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP)),
        activities=(Activity("math", "Math"), Activity("art", "Art")),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pg1", 2, FLEXIBLE),
            TeachingRequirement("r2", "t2", "art", "pg2", 2, FLEXIBLE),
        ),
        days=small_days, periods=small_periods,
    )
    defaults.update(overrides)
    return _problem(**defaults)


def test_reoptimization_never_moves_locked_occurrence():
    from school_timetable.domain.result import SolverStatus
    from school_timetable.scheduling.reoptimize import reoptimize

    problem = _small_full_occupancy_problem()
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r1", "math", "d2", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d1", "q2", ("cx",), "t2", "pg2"),
        _entry("r2", "art", "d2", "q2", ("cx",), "t2", "pg2"),
    )
    schedule = Schedule(entries=entries)
    locked = lock_occurrence(problem, schedule, "r1", "d1", "q1")

    result = reoptimize(problem, locked)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    r1_d1 = [e for e in result.entries if e.requirement_id == "r1" and e.day_id == "d1"]
    assert len(r1_d1) == 1 and r1_d1[0].period_id == "q1"


def test_configured_fixed_placement_remains_fixed_even_when_not_manually_locked():
    from school_timetable.domain.result import SolverStatus
    from school_timetable.scheduling.reoptimize import reoptimize

    problem = _small_full_occupancy_problem(
        fixed_placements=(FixedPlacement("fp1", "r1", TimeSlot("d1", "q1")),),
    )
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r1", "math", "d2", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d1", "q2", ("cx",), "t2", "pg2"),
        _entry("r2", "art", "d2", "q2", ("cx",), "t2", "pg2"),
    )
    schedule = Schedule(entries=entries)  # nothing manually locked

    result = reoptimize(problem, schedule)
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    r1_d1 = [e for e in result.entries if e.requirement_id == "r1" and e.day_id == "d1"]
    assert len(r1_d1) == 1 and r1_d1[0].period_id == "q1"
