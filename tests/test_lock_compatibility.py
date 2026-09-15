"""Safe Configuration Changes, Slice C, Checkpoint 1:
`scheduling.lock_compatibility.classify_locks`.

Fixture style mirrors ``tests/test_editing_locks.py`` exactly (a local
``_problem``/``_entry`` helper pair over a small, fixed 3-day/4-period
scaffold) -- no full ``build_valid_fixture()`` needed for these focused,
single-requirement scenarios.
"""
from __future__ import annotations

import dataclasses

import pytest

from school_timetable.domain.activities import Activity
from school_timetable.domain.blocks import FixedPlacement
from school_timetable.domain.calendar import AcademicYear, Day, Period, TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, LessonBlockPolicy, TeachingRequirement
from school_timetable.domain.resources import Resource, ResourceRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.domain.schedule import OccurrenceKey
from school_timetable.domain.school import School
from school_timetable.scheduling.lock_compatibility import (
    FIXED_PLACEMENT_CONFLICT,
    OCCURRENCE_STRUCTURE_INVALID,
    OCCURRENCE_UNRESOLVABLE,
    REQUIRED_BLOCK_PATTERN_INVALID,
    REQUIREMENT_OR_SLOT_DELETED,
    RESOURCE_CAPACITY_EXCEEDED,
    TEACHER_UNAVAILABLE_AT_SLOT,
    classify_locks,
)

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)
REQUIRED_DOUBLE = LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2,))
REQUIRED_SINGLES = LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(1, 1))

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


def _index(problem: SchedulingProblem) -> ProblemIndex:
    return ProblemIndex(problem)


def _ordinary_setup(**requirement_overrides):
    """One teacher/class/requirement, locked at (d1, q1). Returns
    (problem, entries, key)."""
    requirement = TeachingRequirement("r1", "t1", "math", "pg1", 1, FLEXIBLE, **requirement_overrides)
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""),),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),),
        activities=(Activity("math", "Math"),),
        teaching_requirements=(requirement,),
    )
    entries = (_entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),)
    key = OccurrenceKey("r1", "d1", "q1")
    return problem, entries, key


def test_empty_lock_set_returns_empty_classification():
    problem, entries, _key = _ordinary_setup()
    result = classify_locks(problem, _index(problem), entries, frozenset())
    assert result.compatible_keys == frozenset()
    assert result.incompatible == ()


def test_ordinary_compatible_single_period_occurrence():
    problem, entries, key = _ordinary_setup()
    result = classify_locks(problem, _index(problem), entries, frozenset({key}))
    assert result.compatible_keys == frozenset({key})
    assert result.incompatible == ()


def test_deleted_requirement_is_incompatible():
    problem, entries, key = _ordinary_setup()
    draft = dataclasses.replace(problem, teaching_requirements=())  # r1 removed entirely

    result = classify_locks(draft, _index(draft), entries, frozenset({key}))

    assert result.compatible_keys == frozenset()
    assert len(result.incompatible) == 1
    assert result.incompatible[0].key == key
    assert result.incompatible[0].reason_code == REQUIREMENT_OR_SLOT_DELETED


def test_deleted_period_is_incompatible():
    problem, entries, key = _ordinary_setup()
    draft = dataclasses.replace(problem, periods=tuple(p for p in PERIODS if p.id != "q1"))  # q1 removed

    result = classify_locks(draft, _index(draft), entries, frozenset({key}))

    assert result.compatible_keys == frozenset()
    assert len(result.incompatible) == 1
    assert result.incompatible[0].key == key
    assert result.incompatible[0].reason_code == OCCURRENCE_UNRESOLVABLE


def test_teacher_unavailable_at_locked_slot_is_incompatible():
    problem, entries, key = _ordinary_setup()
    draft = dataclasses.replace(
        problem,
        teacher_availabilities=(TeacherAvailability("t1", "d1", "q1", AvailabilityStatus.UNAVAILABLE),),
    )

    result = classify_locks(draft, _index(draft), entries, frozenset({key}))

    assert result.compatible_keys == frozenset()
    assert len(result.incompatible) == 1
    assert result.incompatible[0].key == key
    assert result.incompatible[0].reason_code == TEACHER_UNAVAILABLE_AT_SLOT


def test_fixed_placement_conflict_is_incompatible():
    problem, entries, key = _ordinary_setup()
    draft = dataclasses.replace(
        problem,
        fixed_placements=(FixedPlacement(id="fp1", requirement_id="r1", slot=TimeSlot("d1", "q2")),),
    )

    result = classify_locks(draft, _index(draft), entries, frozenset({key}))

    assert result.compatible_keys == frozenset()
    assert len(result.incompatible) == 1
    assert result.incompatible[0].key == key
    assert result.incompatible[0].reason_code == FIXED_PLACEMENT_CONFLICT


def test_required_block_pattern_no_longer_valid_is_incompatible():
    # Historically a REQUIRED double (one 2-period block on d1); the draft
    # now requires two separate REQUIRED singles instead -- the historical
    # placement (still a genuinely consecutive q1/q2 pair) no longer
    # matches the draft's own declared pattern.
    requirement = TeachingRequirement("r1", "t1", "math", "pg1", 2, REQUIRED_DOUBLE)
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="T1", last_name=""),),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg1", "PG1", ("cx",), ParticipantGroupRole.WHOLE_CLASS),),
        activities=(Activity("math", "Math"),),
        teaching_requirements=(requirement,),
    )
    entries = (
        _entry("r1", "math", "d1", "q1", ("cx",), "t1", "pg1"),
        _entry("r1", "math", "d1", "q2", ("cx",), "t1", "pg1"),
    )
    key = OccurrenceKey("r1", "d1", "q1")

    draft_requirement = dataclasses.replace(requirement, block_policy=REQUIRED_SINGLES)
    draft = dataclasses.replace(problem, teaching_requirements=(draft_requirement,))

    result = classify_locks(draft, _index(draft), entries, frozenset({key}))

    assert result.compatible_keys == frozenset()
    assert len(result.incompatible) == 1
    assert result.incompatible[0].key == key
    assert result.incompatible[0].reason_code == REQUIRED_BLOCK_PATTERN_INVALID


def test_split_sibling_synchronization_broken_is_incompatible():
    # Historically a lone split branch (german); the draft adds a NEW
    # sibling (russian) to the same split group with no historical entry
    # at all -- the primary's own periods can no longer be shown
    # synchronized with every CURRENT sibling.
    german = TeachingRequirement("german", "t_de", "german", "pg_de", 1, FLEXIBLE, split_group_id="s1")
    problem = _problem(
        teachers=(Teacher(id="t_de", first_name="DE", last_name=""),),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(ParticipantGroup("pg_de", "DE", ("cx",), ParticipantGroupRole.SUBGROUP),),
        activities=(Activity("german", "German"),),
        teaching_requirements=(german,),
    )
    entries = (_entry("german", "german", "d1", "q1", ("cx",), "t_de", "pg_de"),)
    key = OccurrenceKey("german", "d1", "q1")

    russian = TeachingRequirement("russian", "t_ru", "russian", "pg_ru", 1, FLEXIBLE, split_group_id="s1")
    draft = dataclasses.replace(
        problem,
        teachers=problem.teachers + (Teacher(id="t_ru", first_name="RU", last_name=""),),
        participant_groups=problem.participant_groups + (
            ParticipantGroup("pg_ru", "RU", ("cx",), ParticipantGroupRole.SUBGROUP),
        ),
        activities=problem.activities + (Activity("russian", "Russian"),),
        teaching_requirements=(german, russian),
    )

    result = classify_locks(draft, _index(draft), entries, frozenset({key}))

    assert result.compatible_keys == frozenset()
    assert len(result.incompatible) == 1
    assert result.incompatible[0].key == key
    assert result.incompatible[0].reason_code == OCCURRENCE_STRUCTURE_INVALID
    assert "not synchronized" in result.incompatible[0].message


def test_resource_capacity_incompatibility():
    problem, entries, key = _ordinary_setup(resource_requirement=ResourceRequirement("gym"))
    problem = dataclasses.replace(problem, resources=(Resource(id="gym", name="Gym", capacity=1),))
    draft = dataclasses.replace(problem, resources=(Resource(id="gym", name="Gym", capacity=0),))

    result = classify_locks(draft, _index(draft), entries, frozenset({key}))

    assert result.compatible_keys == frozenset()
    assert len(result.incompatible) == 1
    assert result.incompatible[0].key == key
    assert result.incompatible[0].reason_code == RESOURCE_CAPACITY_EXCEEDED


def test_mixture_of_compatible_and_incompatible_locks():
    ok_requirement = TeachingRequirement("r_ok", "t_ok", "math", "pg_ok", 1, FLEXIBLE)
    gone_requirement = TeachingRequirement("r_gone", "t_gone", "science", "pg_gone", 1, FLEXIBLE)
    problem = _problem(
        teachers=(
            Teacher(id="t_ok", first_name="OK", last_name=""),
            Teacher(id="t_gone", first_name="Gone", last_name=""),
        ),
        class_sections=(ClassSection("cx", "CX"),),
        participant_groups=(
            ParticipantGroup("pg_ok", "OK", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg_gone", "Gone", ("cx",), ParticipantGroupRole.WHOLE_CLASS),
        ),
        activities=(Activity("math", "Math"), Activity("science", "Science")),
        teaching_requirements=(ok_requirement, gone_requirement),
    )
    entries = (
        _entry("r_ok", "math", "d1", "q1", ("cx",), "t_ok", "pg_ok"),
        _entry("r_gone", "science", "d2", "q1", ("cx",), "t_gone", "pg_gone"),
    )
    ok_key = OccurrenceKey("r_ok", "d1", "q1")
    gone_key = OccurrenceKey("r_gone", "d2", "q1")

    draft = dataclasses.replace(problem, teaching_requirements=(ok_requirement,))  # r_gone removed

    result = classify_locks(draft, _index(draft), entries, frozenset({ok_key, gone_key}))

    assert result.compatible_keys == frozenset({ok_key})
    assert len(result.incompatible) == 1
    assert result.incompatible[0].key == gone_key
    assert result.incompatible[0].reason_code == REQUIREMENT_OR_SLOT_DELETED


def test_natural_id_identity_is_preserved_exactly():
    problem, entries, key = _ordinary_setup()
    result = classify_locks(problem, _index(problem), entries, frozenset({key}))

    (compatible_key,) = result.compatible_keys
    assert compatible_key.requirement_id == "r1"
    assert compatible_key.day_id == "d1"
    assert compatible_key.anchor_period_id == "q1"
    assert compatible_key is key or compatible_key == key


def test_classification_is_deterministic_across_repeated_calls():
    problem, entries, key = _ordinary_setup()
    draft = dataclasses.replace(problem, teaching_requirements=())

    first = classify_locks(draft, _index(draft), entries, frozenset({key}))
    second = classify_locks(draft, _index(draft), entries, frozenset({key}))

    assert first.compatible_keys == second.compatible_keys
    assert first.incompatible == second.incompatible
    assert first.incompatible[0].reason_code == second.incompatible[0].reason_code == REQUIREMENT_OR_SLOT_DELETED


@pytest.mark.parametrize(
    "weekly_periods,policy,history_slots,fixed_slots,conflict",
    [
        # Separate FLEXIBLE occurrences, including on the same day.
        (2, FLEXIBLE, (("d1", "q1"), ("d2", "q1")), (("d2", "q1"),), False),
        (2, FLEXIBLE, (("d1", "q1"), ("d1", "q2")), (("d1", "q2"),), False),
        # A fixed and locked slot is one lesson, even with duplicate fixed rows.
        (1, FLEXIBLE, (("d1", "q1"),), (("d1", "q1"), ("d1", "q1")), False),
        # Multiple fixed lessons consume the budget; overlapping pins count once.
        (3, FLEXIBLE, (("d1", "q1"), ("d2", "q1"), ("d3", "q1")),
         (("d2", "q1"), ("d3", "q1")), False),
        (2, FLEXIBLE, (("d1", "q1"), ("d2", "q1")),
         (("d2", "q1"), ("d3", "q1")), True),
        (2, FLEXIBLE, (("d1", "q1"), ("d2", "q1")),
         (("d1", "q1"), ("d2", "q1")), False),
        # A REQUIRED double is one occurrence; either member may be fixed.
        (2, REQUIRED_DOUBLE, (("d1", "q1"), ("d1", "q2")), (("d1", "q1"),), False),
        (2, REQUIRED_DOUBLE, (("d1", "q1"), ("d1", "q2")), (("d1", "q2"),), False),
        (2, REQUIRED_DOUBLE, (("d1", "q1"), ("d1", "q2")),
         (("d1", "q1"), ("d1", "q2")), False),
        (2, REQUIRED_DOUBLE, (("d1", "q1"), ("d1", "q2")), (("d2", "q1"),), True),
        # Two doubles: fixed member periods on one day consume only one block.
        (4, LessonBlockPolicy(BlockPolicyMode.REQUIRED, (2, 2)),
         (("d1", "q1"), ("d1", "q2"), ("d2", "q1"), ("d2", "q2")),
         (("d2", "q1"), ("d2", "q2")), False),
        # Four forced periods fit weekly_periods, but THREE days need >2 blocks.
        (4, LessonBlockPolicy(BlockPolicyMode.REQUIRED, (2, 2)),
         (("d1", "q1"), ("d1", "q2"), ("d2", "q1"), ("d2", "q2")),
         (("d2", "q1"), ("d3", "q1")), True),
        # PREFERRED block_sizes is not a hard occurrence-count limit.
        (2, LessonBlockPolicy(BlockPolicyMode.PREFERRED, (2,)),
         (("d1", "q1"), ("d2", "q1")), (("d2", "q1"),), False),
    ],
)
def test_fixed_placements_count_distinct_lessons_and_required_block_days(
    weekly_periods, policy, history_slots, fixed_slots, conflict,
):
    problem, _, key = _ordinary_setup()
    requirement = dataclasses.replace(
        problem.teaching_requirements[0], weekly_periods=weekly_periods, block_policy=policy,
    )
    problem = dataclasses.replace(
        problem, teaching_requirements=(requirement,),
        fixed_placements=tuple(FixedPlacement(f"fp{i}", "r1", TimeSlot(*slot))
                               for i, slot in enumerate(fixed_slots)),
    )
    entries = tuple(_entry("r1", "math", day, period, ("cx",), "t1", "pg1")
                    for day, period in history_slots)
    result = classify_locks(problem, _index(problem), entries, frozenset({key}))
    if conflict:
        assert result.compatible_keys == frozenset()
        assert [item.reason_code for item in result.incompatible] == [FIXED_PLACEMENT_CONFLICT]
    else:
        assert result.compatible_keys == frozenset({key})
        assert result.incompatible == ()


@pytest.mark.parametrize("earlier_reason", [
    REQUIREMENT_OR_SLOT_DELETED, OCCURRENCE_UNRESOLVABLE, TEACHER_UNAVAILABLE_AT_SLOT,
])
def test_fixed_placement_conflict_preserves_earlier_reason_precedence(earlier_reason):
    problem, entries, key = _ordinary_setup()
    problem = dataclasses.replace(
        problem, fixed_placements=(FixedPlacement("fp", "r1", TimeSlot("d2", "q1")),),
    )
    if earlier_reason == REQUIREMENT_OR_SLOT_DELETED:
        problem = dataclasses.replace(problem, teaching_requirements=())
    elif earlier_reason == OCCURRENCE_UNRESOLVABLE:
        problem = dataclasses.replace(problem, periods=PERIODS[1:])
    else:
        problem = dataclasses.replace(
            problem, teacher_availabilities=(TeacherAvailability("t1", "d1", "q1", AvailabilityStatus.UNAVAILABLE),),
        )
    result = classify_locks(problem, _index(problem), entries, frozenset({key}))
    assert [item.reason_code for item in result.incompatible] == [earlier_reason]


def test_history_8b_fixed_tuesday_and_locked_wednesday_remain_hard_and_feasible():
    from school_timetable.domain.schedule import Schedule
    from school_timetable.fixtures.valid_fixture import build_valid_fixture
    from school_timetable.scheduling.editing import lock_occurrence
    from school_timetable.scheduling.lock_compatibility import resolve_compatible_members
    from school_timetable.scheduling.solver import solve
    from school_timetable.verification.verifier import verify

    problem = build_valid_fixture()
    fixed = FixedPlacement("history-tuesday", "history_8b", TimeSlot("tue", "p4"))
    wednesday = FixedPlacement("history-wednesday", "history_8b", TimeSlot("wed", "p1"))
    # Construct verified history with these exact placements, independent of solver tie-breaking.
    historical_problem = dataclasses.replace(problem, fixed_placements=problem.fixed_placements + (fixed, wednesday))
    historical_result = solve(historical_problem)
    assert historical_result.is_success
    assert verify(historical_problem, historical_result.entries).passed
    problem = dataclasses.replace(problem, fixed_placements=problem.fixed_placements + (fixed,))
    schedule = lock_occurrence(problem, Schedule(entries=historical_result.entries), "history_8b", "wed", "p1")
    index = ProblemIndex(problem)
    classification = classify_locks(problem, index, schedule.entries, schedule.locked_occurrences)
    assert classification.incompatible == ()
    assert classification.compatible_keys == frozenset({OccurrenceKey("history_8b", "wed", "p1")})
    pins = resolve_compatible_members(problem, index, schedule.entries, classification.compatible_keys)
    assert pins == frozenset({("history_8b", "wed", "p1")})
    result = solve(problem, hard_pins=pins)
    assert result.is_success
    assert verify(problem, result.entries).passed
    slots = {(e.day_id, e.period_id) for e in result.entries if e.requirement_id == "history_8b"}
    assert {("tue", "p4"), ("wed", "p1")} <= slots


def _required_lock_setup(pattern, historical_blocks, locked_days):
    problem, _, _ = _ordinary_setup()
    req = dataclasses.replace(
        problem.teaching_requirements[0], weekly_periods=sum(pattern),
        block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, pattern),
    )
    problem = dataclasses.replace(problem, teaching_requirements=(req,))
    entries = tuple(_entry("r1", "math", day, period, ("cx",), "t1", "pg1")
                    for day, periods in historical_blocks for period in periods)
    keys = frozenset(OccurrenceKey("r1", day, periods[0])
                     for day, periods in historical_blocks if day in locked_days)
    return problem, entries, keys


@pytest.mark.parametrize("pattern", [(2, 1, 1), (1, 2, 1), (1, 1, 2)])
@pytest.mark.parametrize("locked_days", [("d1",), ("d1", "d2"), ("d1", "d2", "d3")])
def test_required_locked_sizes_are_a_multiset_subset(pattern, locked_days):
    problem, entries, keys = _required_lock_setup(
        pattern, (("d1", ("q1", "q2")), ("d2", ("q1",)), ("d3", ("q1",))), locked_days,
    )
    result = classify_locks(problem, _index(problem), entries, keys)
    assert result.compatible_keys == keys
    assert result.incompatible == ()


def test_required_pattern_growth_ignores_unlocked_historical_occurrences():
    # Old (2, 1) becomes (2, 1, 1); only the double is locked.
    problem, entries, keys = _required_lock_setup(
        (2, 1, 1), (("d1", ("q1", "q2")), ("d2", ("q1",))), ("d1",),
    )
    expected = classify_locks(problem, _index(problem), entries, keys)
    assert expected.compatible_keys == keys
    assert expected.incompatible == ()
    double_only = tuple(e for e in entries if e.day_id == "d1")
    # Missing/moved/additional unlocked blocks have no bearing on the locked double.
    extra_unlocked = tuple(_entry("r1", "math", "d3", p, ("cx",), "t1", "pg1") for p in ("q1", "q3"))
    for history in (double_only, double_only + extra_unlocked, entries + extra_unlocked):
        assert classify_locks(problem, _index(problem), history, keys) == expected


def test_required_multiplicity_overflow_retains_first_natural_key_deterministically():
    from itertools import permutations

    problem, entries, keys = _required_lock_setup(
        (2, 1, 1), (("d2", ("q1", "q2")), ("d1", ("q1", "q2"))), ("d1", "d2"),
    )
    for ordered_keys in permutations(keys):
        for history in (entries, tuple(reversed(entries))):
            result = classify_locks(problem, _index(problem), history, frozenset(ordered_keys))
            assert result.compatible_keys == frozenset({OccurrenceKey("r1", "d1", "q1")})
            assert len(result.incompatible) == 1
            item = result.incompatible[0]
            assert item.key == OccurrenceKey("r1", "d2", "q1")
            assert item.reason_code == REQUIRED_BLOCK_PATTERN_INVALID
            assert "1 size-2 block(s) already retained" in item.message
            assert "1 allowed" in item.message


def test_required_pattern_rejects_a_locked_triple_when_only_double_and_singles_allowed():
    problem, entries, keys = _required_lock_setup(
        (2, 1, 1), (("d1", ("q1", "q2", "q3")),), ("d1",),
    )
    problem = dataclasses.replace(problem, periods=tuple(dataclasses.replace(p, block_id="am") for p in PERIODS))
    result = classify_locks(problem, _index(problem), entries, keys)
    assert [item.reason_code for item in result.incompatible] == [REQUIRED_BLOCK_PATTERN_INVALID]
    assert result.compatible_keys == frozenset()


def test_directly_incompatible_lock_does_not_consume_required_pattern_capacity():
    problem, entries, keys = _required_lock_setup(
        (2, 1, 1), (("d1", ("q1", "q2")), ("d2", ("q1", "q2"))), ("d1", "d2"),
    )
    problem = dataclasses.replace(
        problem, teacher_availabilities=(TeacherAvailability("t1", "d1", "q1", AvailabilityStatus.UNAVAILABLE),),
    )
    result = classify_locks(problem, _index(problem), entries, keys)
    assert result.compatible_keys == frozenset({OccurrenceKey("r1", "d2", "q1")})
    assert [(item.key.day_id, item.reason_code) for item in result.incompatible] == [
        ("d1", TEACHER_UNAVAILABLE_AT_SLOT),
    ]


def test_split_sibling_keys_share_required_block_capacity():
    problem, entries, keys = _required_lock_setup(
        (2, 1, 1), (("d1", ("q1", "q2")), ("d2", ("q1", "q2"))), ("d1", "d2"),
    )
    first = dataclasses.replace(problem.teaching_requirements[0], split_group_id="split")
    sibling = dataclasses.replace(first, id="r2", teacher_id="t2")
    problem = dataclasses.replace(
        problem, teaching_requirements=(first, sibling),
        teachers=problem.teachers + (Teacher("t2", "T2", ""),),
    )
    entries += tuple(dataclasses.replace(e, requirement_id="r2", teacher_id="t2") for e in entries)
    keys |= frozenset(OccurrenceKey("r2", k.day_id, k.anchor_period_id) for k in keys)
    result = classify_locks(problem, _index(problem), entries, keys)
    assert result.compatible_keys == frozenset({OccurrenceKey(r, "d1", "q1") for r in ("r1", "r2")})
    assert {item.key for item in result.incompatible} == {OccurrenceKey(r, "d2", "q1") for r in ("r1", "r2")}
    assert all(item.reason_code == REQUIRED_BLOCK_PATTERN_INVALID for item in result.incompatible)


def test_required_pattern_growth_preserves_locked_double_in_normal_solve():
    from school_timetable.domain.schedule import Schedule
    from school_timetable.scheduling.editing import lock_occurrence
    from school_timetable.scheduling.lock_compatibility import resolve_compatible_members
    from school_timetable.scheduling.solver import solve
    from school_timetable.verification.verifier import verify

    problem, _, _ = _ordinary_setup()
    requirement = dataclasses.replace(
        problem.teaching_requirements[0], weekly_periods=3,
        block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, (2, 1)),
    )
    filler = dataclasses.replace(requirement, id="filler", teacher_id="t2", block_policy=FLEXIBLE)
    problem = dataclasses.replace(
        problem, periods=PERIODS[:2], teaching_requirements=(requirement, filler),
        teachers=problem.teachers + (Teacher("t2", "T2", ""),),
        fixed_placements=(FixedPlacement("double-1", "r1", TimeSlot("d1", "q1")),
                          FixedPlacement("double-2", "r1", TimeSlot("d1", "q2"))),
    )
    history = solve(problem)
    assert history.is_success
    assert verify(problem, history.entries).passed
    locked = lock_occurrence(problem, Schedule(entries=history.entries), "r1", "d1", "q1")
    draft = dataclasses.replace(
        problem, fixed_placements=(), teaching_requirements=(
            dataclasses.replace(requirement, weekly_periods=4,
                                block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, (2, 1, 1))),
            dataclasses.replace(filler, weekly_periods=2),
        ),
    )
    classification = classify_locks(draft, _index(draft), locked.entries, locked.locked_occurrences)
    assert classification.compatible_keys == locked.locked_occurrences
    assert classification.incompatible == ()
    pins = resolve_compatible_members(draft, _index(draft), locked.entries, classification.compatible_keys)
    assert pins == frozenset({("r1", "d1", "q1"), ("r1", "d1", "q2")})
    regenerated = solve(draft, hard_pins=pins)
    assert regenerated.is_success
    assert verify(draft, regenerated.entries).passed
    assert pins <= {(e.requirement_id, e.day_id, e.period_id) for e in regenerated.entries}
