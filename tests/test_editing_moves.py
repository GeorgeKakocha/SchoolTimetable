"""Phase 2C: manual move validation and application.

Each test builds a small, purpose-built problem + hand-constructed
Schedule so the specific rule under test is isolated and unambiguous.
``validate_move`` independently verifies the input schedule is HARD-valid
before reasoning about a move at all, so every problem here is topped up
to full class occupancy via ``editing_test_support.fill_occupancy`` --
using a dedicated, inert filler requirement per class that never
interacts with the specific rule under test.
"""
from __future__ import annotations

from editing_test_support import fill_occupancy

from school_timetable.domain.activities import Activity, ActivityKind
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
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.domain.schedule import Schedule
from school_timetable.domain.school import School
from school_timetable.scheduling.editing import apply_move, lock_occurrence, validate_move

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)

DAYS = tuple(Day(id=f"d{i}", name=f"Day{i}", index=i - 1) for i in range(1, 4))
# Two structural blocks per day (like the real lunch boundary): q1,q2 |
# q3,q4, so REQUIRED-block/window tests have a genuine break to respect.
PERIODS = (
    Period(id="q1", name="Q1", index=0, block_id="am"),
    Period(id="q2", name="Q2", index=1, block_id="am"),
    Period(id="q3", name="Q3", index=2, block_id="pm"),
    Period(id="q4", name="Q4", index=3, block_id="pm"),
)


def _entry(requirement_id, activity_id, day_id, period_id, class_sections, teacher_id, group_id, resource_id=None):
    return ScheduleEntry(
        source=EntrySource.REQUIREMENT, activity_id=activity_id, day_id=day_id, period_id=period_id,
        class_sections=class_sections, teacher_id=teacher_id, participant_group_id=group_id,
        resource_id=resource_id, requirement_id=requirement_id,
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


def _full(problem: SchedulingProblem, entries: tuple[ScheduleEntry, ...]) -> tuple[SchedulingProblem, Schedule]:
    filled_problem, filled_entries = fill_occupancy(problem, entries)
    return filled_problem, Schedule(entries=filled_entries)


# ---------------------------------------------------------------------------
# 1. Valid ordinary move accepted
# ---------------------------------------------------------------------------

def test_valid_ordinary_move_accepted():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(
            ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP),
        ),
        activities=(Activity("math", "Math"), Activity("art", "Art")),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE),
            TeachingRequirement("r2", "t2", "art", "pg2", 1, FLEXIBLE),
        ),
    )
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d2", "q1", ("cx",), "t2", "pg2"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d2", "q1")
    assert result.allowed, result.violations

    new_schedule = apply_move(problem, schedule, result)
    r1_slot = [(e.day_id, e.period_id) for e in new_schedule.entries if e.requirement_id == "r1"]
    r2_slot = [(e.day_id, e.period_id) for e in new_schedule.entries if e.requirement_id == "r2"]
    assert r1_slot == [("d2", "q1")]
    assert r2_slot == [("d1", "q1")]


# ---------------------------------------------------------------------------
# 2. Teacher collision rejected
# ---------------------------------------------------------------------------

def test_teacher_collision_rejected():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection("cx", "CX"), ClassSection("cy", "CY")),
        participant_groups=(
            ParticipantGroup("pgx", "PGX", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pgy", "PGY", ("cy",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pgx2", "PGX2", ("cx",), ParticipantGroupRole.SUBGROUP),
        ),
        activities=(Activity("math", "Math"),),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pgx", 1, FLEXIBLE),
            TeachingRequirement("r2", "t1", "math", "pgy", 1, FLEXIBLE),
            TeachingRequirement("r3", "t2", "math", "pgx2", 1, FLEXIBLE),
        ),
    )
    # r1 (t1, cx) at d1/q1 is the source. r3 (t2, cx) at d3/q1 is a clean
    # swap partner (same class, different teacher). r2 (t1, cy) sits
    # untouched at d3/q1 too (a different class, so no conflict there
    # today). Swapping r1 into d3/q1 would put t1 at d3/q1 -- but t1 is
    # already teaching r2 (cy) there.
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pgx"),
        _entry("r2", "math", "d3", "q1", ("cy",), "t1", "pgy"),
        _entry("r3", "math", "d3", "q1", ("cx",), "t2", "pgx2"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d3", "q1")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "TEACHER_CONFLICT" in codes


# ---------------------------------------------------------------------------
# 3. Class/group collision rejected: target occupant covers a different,
#    incompatible set of classes (a merged lesson vs. a single-class mover).
# ---------------------------------------------------------------------------

def test_target_class_mismatch_rejected():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection("cx", "CX"), ClassSection("cz", "CZ")),
        participant_groups=(
            ParticipantGroup("pgx", "PGX", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg_merged", "Merged", ("cx", "cz"), ParticipantGroupRole.MERGED_CLASSES),
        ),
        activities=(Activity("math", "Math"), Activity("civics", "Civics")),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pgx", 1, FLEXIBLE),
            TeachingRequirement("merged", "t2", "civics", "pg_merged", 1, FLEXIBLE),
        ),
    )
    # r1 covers only class cx. "merged" covers (cx, cz) together at d2/q1.
    # Swapping r1 into that slot would need to displace "merged" for BOTH
    # its classes, but r1 only has room for cx -- a genuine class-set
    # mismatch, not just an empty-slot artifact of an under-occupied fixture.
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pgx"),
        _entry("merged", "civics", "d2", "q1", ("cx", "cz"), "t2", "pg_merged"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d2", "q1")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "CLASS_OCCUPANCY_CONFLICT" in codes


# ---------------------------------------------------------------------------
# 4. Unavailable teacher target rejected
# ---------------------------------------------------------------------------

def test_unavailable_teacher_target_rejected():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP)),
        activities=(Activity("math", "Math"), Activity("art", "Art")),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE),
            TeachingRequirement("r2", "t2", "art", "pg2", 1, FLEXIBLE),
        ),
        teacher_availabilities=(TeacherAvailability("t1", "d2", "q1", AvailabilityStatus.UNAVAILABLE),),
    )
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d2", "q1", ("cx",), "t2", "pg2"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d2", "q1")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "TEACHER_UNAVAILABLE" in codes


# ---------------------------------------------------------------------------
# 5. Gym capacity conflict rejected
# ---------------------------------------------------------------------------

def test_resource_capacity_conflict_rejected():
    gym = ResourceRequirement(resource_id="gym")
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name=""), Teacher(id="t3", first_name="T3", last_name="")),
        class_sections=(ClassSection("cx", "CX"), ClassSection("cy", "CY")),
        participant_groups=(
            ParticipantGroup("pgx", "PGX", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pgx2", "PGX2", ("cx",), ParticipantGroupRole.SUBGROUP),
            ParticipantGroup("pgy", "PGY", ("cy",), ParticipantGroupRole.WHOLE_CLASS),
        ),
        activities=(Activity("sport", "Sport"), Activity("dance", "Dance"), Activity("math", "Math")),
        resources=(Resource("gym", "Gym", capacity=1),),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "sport", "pgx", 1, FLEXIBLE, resource_requirement=gym),
            TeachingRequirement("r2", "t2", "dance", "pgy", 1, FLEXIBLE, resource_requirement=gym),
            TeachingRequirement("r3", "t3", "math", "pgx2", 1, FLEXIBLE),
        ),
    )
    # r1 (cx, sport, gym) at d1/q1. r2 (cy, dance, gym) at d1/q2 -- a
    # different class already using the gym at d1/q2. r3 (cx, plain math)
    # also sits at d1/q2 (classes are independent grids, so cx and cy can
    # both have entries at the same slot). Swapping r1 into d1/q2 (against
    # r3, same class cx) would put a second gym user at d1/q2 alongside r2.
    entries = (
        _entry("r1", "sport", "d1", "q1", ("cx",), "t1", "pgx", resource_id="gym"),
        _entry("r2", "dance", "d1", "q2", ("cy",), "t2", "pgy", resource_id="gym"),
        _entry("r3", "math", "d1", "q2", ("cx",), "t3", "pgx2"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d1", "q2")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "RESOURCE_CAPACITY" in codes


# ---------------------------------------------------------------------------
# 6. Fixed occurrence move rejected
# ---------------------------------------------------------------------------

def test_fixed_occurrence_move_rejected():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP)),
        activities=(Activity("math", "Math"), Activity("art", "Art")),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE),
            TeachingRequirement("r2", "t2", "art", "pg2", 1, FLEXIBLE),
        ),
        fixed_placements=(FixedPlacement("fp1", "r1", TimeSlot("d1", "q1")),),
    )
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d2", "q1", ("cx",), "t2", "pg2"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d2", "q1")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "FIXED_PLACEMENT" in codes


def test_target_side_fixed_occurrence_move_rejected():
    """Same protection, but the SOURCE is free and it's the TARGET occupant
    that is fixed -- displacing it must be rejected too, not just moving
    a fixed occurrence directly."""
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP)),
        activities=(Activity("math", "Math"), Activity("art", "Art")),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE),
            TeachingRequirement("r2", "t2", "art", "pg2", 1, FLEXIBLE),
        ),
        fixed_placements=(FixedPlacement("fp1", "r2", TimeSlot("d2", "q1")),),
    )
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d2", "q1", ("cx",), "t2", "pg2"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d2", "q1")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "FIXED_PLACEMENT" in codes


def test_target_side_locked_occurrence_move_rejected():
    """Same protection, but the SOURCE is free and it's the TARGET occupant
    that is manually locked -- displacing it must be rejected too."""
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP)),
        activities=(Activity("math", "Math"), Activity("art", "Art")),
        teaching_requirements=(
            TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE),
            TeachingRequirement("r2", "t2", "art", "pg2", 1, FLEXIBLE),
        ),
    )
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d2", "q1", ("cx",), "t2", "pg2"),
    )
    problem, schedule = _full(problem, entries)
    schedule = lock_occurrence(problem, schedule, "r2", "d2", "q1")

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d2", "q1")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "LOCKED_OCCURRENCE" in codes


# ---------------------------------------------------------------------------
# 7. Reserved Club conflict rejected
# ---------------------------------------------------------------------------

def test_reserved_block_conflict_rejected():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""),),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),),
        activities=(Activity("math", "Math"), Activity("club_chess", "Chess", kind=ActivityKind.CLUB)),
        teaching_requirements=(TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE),),
        reserved_blocks=(
            ReservedBlock("club1", "Chess Club", "club_chess", ("cx",), (TimeSlot("d2", "q1"),)),
        ),
    )
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        ScheduleEntry(
            source=EntrySource.RESERVED_BLOCK, activity_id="club_chess", day_id="d2", period_id="q1",
            class_sections=("cx",), reserved_block_id="club1",
        ),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "r1", "d1", "q1", "d2", "q1")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "RESERVED_BLOCK_CONFLICT" in codes


# ---------------------------------------------------------------------------
# 8 & 9. REQUIRED block partial move rejected / whole move accepted
# ---------------------------------------------------------------------------

def _required_block_problem():
    return _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP)),
        activities=(Activity("math", "Math"), Activity("art", "Art")),
        teaching_requirements=(
            TeachingRequirement(
                "r1", "t1", "math", "pg1", 2, LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2,)),
            ),
            TeachingRequirement(
                "r2", "t2", "art", "pg2", 2, LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2,)),
            ),
        ),
    )


def test_required_block_partial_move_rejected():
    problem = _required_block_problem()
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r1", "math", "d1", "q2", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d2", "q3", ("cx",), "t2", "pg2"),
        _entry("r2", "art", "d2", "q4", ("cx",), "t2", "pg2"),
    )
    problem, schedule = _full(problem, entries)

    # Try to move r1's whole block to a target that cannot fit a 2-period
    # window at all (d2/q4 is the LAST period of the "pm" run, so a
    # 2-length window starting there would run off the run entirely).
    result = validate_move(problem, schedule, "r1", "d1", "q1", "d2", "q4")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "TARGET_WINDOW_INVALID" in codes


def test_required_block_whole_move_accepted_when_valid():
    problem = _required_block_problem()
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r1", "math", "d1", "q2", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d2", "q3", ("cx",), "t2", "pg2"),
        _entry("r2", "art", "d2", "q4", ("cx",), "t2", "pg2"),
    )
    problem, schedule = _full(problem, entries)

    # Move r1's whole 2-period block from (d1,q1-q2) to (d2,q3-q4) --
    # swaps with r2's whole block.
    result = validate_move(problem, schedule, "r1", "d1", "q1", "d2", "q3")
    assert result.allowed, result.violations
    new_schedule = apply_move(problem, schedule, result)
    r1_periods = sorted(e.period_id for e in new_schedule.entries if e.requirement_id == "r1" and e.day_id == "d2")
    r2_periods = sorted(e.period_id for e in new_schedule.entries if e.requirement_id == "r2" and e.day_id == "d1")
    assert r1_periods == ["q3", "q4"]
    assert r2_periods == ["q1", "q2"]


# ---------------------------------------------------------------------------
# 10 & 11. Split occurrence moves both branches together / rejected if invalid for either
# ---------------------------------------------------------------------------

def _split_problem(t_german_unavailable_at=None):
    availabilities = ()
    if t_german_unavailable_at:
        availabilities = (TeacherAvailability("t_de", *t_german_unavailable_at, AvailabilityStatus.UNAVAILABLE),)
    return _problem(
        teachers=(Teacher(id="t_de", first_name="DE", last_name=""), Teacher(id="t_ru", first_name="RU", last_name=""), Teacher(id="t3", first_name="T3", last_name="")),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(
            ParticipantGroup("pg_de", "DE branch", ("cx",), ParticipantGroupRole.SUBGROUP),
            ParticipantGroup("pg_ru", "RU branch", ("cx",), ParticipantGroupRole.SUBGROUP),
            ParticipantGroup("pg3", "PG3", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
        ),
        activities=(Activity("german", "German"), Activity("russian", "Russian"), Activity("math", "Math")),
        teaching_requirements=(
            TeachingRequirement("german", "t_de", "german", "pg_de", 1, FLEXIBLE, split_group_id="split1"),
            TeachingRequirement("russian", "t_ru", "russian", "pg_ru", 1, FLEXIBLE, split_group_id="split1"),
            TeachingRequirement("r3", "t3", "math", "pg3", 1, FLEXIBLE),
        ),
        teacher_availabilities=availabilities,
    )


def test_split_occurrence_moves_both_branches_together():
    problem = _split_problem()
    entries = (
        _entry("german", "german", "d1", "q1", ("cx",), "t_de", "pg_de"),
        _entry("russian", "russian", "d1", "q1", ("cx",), "t_ru", "pg_ru"),
        _entry("r3", "math", "d2", "q1", ("cx",), "t3", "pg3"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "german", "d1", "q1", "d2", "q1")
    assert result.allowed, result.violations
    new_schedule = apply_move(problem, schedule, result)

    german_slot = [(e.day_id, e.period_id) for e in new_schedule.entries if e.requirement_id == "german"]
    russian_slot = [(e.day_id, e.period_id) for e in new_schedule.entries if e.requirement_id == "russian"]
    assert german_slot == [("d2", "q1")]
    assert russian_slot == [("d2", "q1")]  # moved together, still synchronized


def test_split_move_rejected_if_target_invalid_for_either_branch():
    # t_de is unavailable at the intended target slot.
    problem = _split_problem(t_german_unavailable_at=("d2", "q1"))
    entries = (
        _entry("german", "german", "d1", "q1", ("cx",), "t_de", "pg_de"),
        _entry("russian", "russian", "d1", "q1", ("cx",), "t_ru", "pg_ru"),
        _entry("r3", "math", "d2", "q1", ("cx",), "t3", "pg3"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "german", "d1", "q1", "d2", "q1")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "TEACHER_UNAVAILABLE" in codes


# ---------------------------------------------------------------------------
# 12. Merged-group move blocks/uses both participating classes
# ---------------------------------------------------------------------------

def test_merged_group_move_uses_both_classes():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection("cx", "CX"), ClassSection("cy", "CY")),
        participant_groups=(
            ParticipantGroup("pg_merged", "Merged", ("cx", "cy"), ParticipantGroupRole.MERGED_CLASSES),
            ParticipantGroup("pg2", "PG2", ("cx", "cy"), ParticipantGroupRole.MERGED_CLASSES),
        ),
        activities=(Activity("civics", "Civics"),),
        teaching_requirements=(
            TeachingRequirement("merged1", "t1", "civics", "pg_merged", 1, FLEXIBLE),
            TeachingRequirement("merged2", "t2", "civics", "pg2", 1, FLEXIBLE),
        ),
    )
    entries = (
        _entry("merged1", "civics", "d1", "q1", ("cx", "cy"), "t1", "pg_merged"),
        _entry("merged2", "civics", "d2", "q1", ("cx", "cy"), "t2", "pg2"),
    )
    problem, schedule = _full(problem, entries)

    result = validate_move(problem, schedule, "merged1", "d1", "q1", "d2", "q1")
    assert result.allowed, result.violations
    new_schedule = apply_move(problem, schedule, result)
    merged1_entry = next(e for e in new_schedule.entries if e.requirement_id == "merged1")
    assert set(merged1_entry.class_sections) == {"cx", "cy"}
    assert (merged1_entry.day_id, merged1_entry.period_id) == ("d2", "q1")


# ---------------------------------------------------------------------------
# 13. max_periods_per_day violation rejected
# ---------------------------------------------------------------------------

def test_max_periods_per_day_violation_rejected():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("cx",), ParticipantGroupRole.SUBGROUP)),
        activities=(Activity("math", "Math"), Activity("art", "Art")),
        teaching_requirements=(
            TeachingRequirement(
                "r1", "t1", "math", "pg1", 2, FLEXIBLE,
                distribution_policy=DistributionPolicy(max_periods_per_day=1),
            ),
            TeachingRequirement("r2", "t2", "art", "pg2", 1, FLEXIBLE),
        ),
    )
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r1", "math", "d2", "q1", ("cx",), "t1", "pg1"),
        _entry("r2", "art", "d1", "q2", ("cx",), "t2", "pg2"),
    )
    problem, schedule = _full(problem, entries)

    # Move r1's d2/q1 occurrence to swap with r2's d1/q2 slot -- this
    # would give r1 TWO periods on d1 (q1 and q2), exceeding max_periods_per_day=1.
    result = validate_move(problem, schedule, "r1", "d2", "q1", "d1", "q2")
    assert not result.allowed
    codes = {v.code for v in result.violations}
    assert "MAX_PERIODS_PER_DAY" in codes


# ---------------------------------------------------------------------------
# 14. Applying an accepted move produces a schedule that passes independent
#     verification (real solver-generated fixture).
# ---------------------------------------------------------------------------

def test_applying_accepted_move_passes_independent_verification():
    from school_timetable.domain.indexing import ProblemIndex
    from school_timetable.fixtures.valid_fixture import build_valid_fixture
    from school_timetable.scheduling.editing import find_logical_occurrence
    from school_timetable.scheduling.options import SolverOptions
    from school_timetable.scheduling.solver import solve
    from school_timetable.verification.verifier import verify

    problem = build_valid_fixture()
    result = solve(problem, SolverOptions(random_seed=7, num_search_workers=1))
    assert result.status.value in ("OPTIMAL", "FEASIBLE")
    schedule = Schedule(entries=result.entries)
    index = ProblemIndex(problem)

    # Find any two simple (length-1, non-split) occurrences on different
    # days for the same class, and try swaps until one validates -- the
    # dense fixture means not every pair is conflict-free, but at least
    # one should be (proven empirically during development).
    simple_occs = []
    seen = set()
    for e in schedule.entries:
        if e.requirement_id is None or (e.requirement_id, e.day_id, e.period_id) in seen:
            continue
        occ = find_logical_occurrence(problem, index, schedule, e.requirement_id, e.day_id, e.period_id)
        if occ.length == 1 and len(occ.requirement_ids) == 1:
            simple_occs.append((e.requirement_id, e.day_id, e.period_id))
        for m in occ.members:
            seen.add((m.requirement_id, m.day_id, m.period_id))

    applied = False
    for (r1, d1, p1) in simple_occs:
        for (r2, d2, p2) in simple_occs:
            if r1 == r2 or d1 == d2:
                continue
            move_result = validate_move(problem, schedule, r1, d1, p1, d2, p2, index=index)
            if move_result.allowed and {e.requirement_id for e in move_result.plan.removed_entries} == {r1, r2}:
                new_schedule = apply_move(problem, schedule, move_result)
                report = verify(problem, new_schedule.entries)
                assert report.passed, report.violations
                applied = True
                break
        if applied:
            break

    assert applied, "expected at least one valid swap to exist in the dense fixture"
